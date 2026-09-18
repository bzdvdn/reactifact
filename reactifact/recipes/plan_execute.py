"""recipes.plan_execute — sequential plan → execute → finish (§24, §42, §69).

Ported from `examples/plan_execute`, generalized: that example hard-wires
its own `Goal`/`PlanStep`/`StepResult`/`FinalAnswer` models directly into
the control flow (`isinstance(goal.data, Goal)`, `PlanStep(goal=..., index=...)`).
Here the control flow — ordering, gating each step on its predecessor's
result, idempotent re-entry, completion detection — is owned by the recipe;
the domain owns the three actual decisions (`plan`, `execute_step`, `finish`)
and its own artifact shapes, LLM-free by design (§67) — nothing here calls
an LLM, that's on your hooks.

    class Flow(PlanExecute[Goal, PlanStep, StepResult, FinalAnswer]):
        goal_type = Goal
        step_type = PlanStep
        result_type = StepResult
        final_type = FinalAnswer

        async def plan(self, context, goal) -> list[PlanStep]: ...
        async def execute_step(self, context, step, history) -> StepResult: ...
        async def finish(self, context, goal, results) -> FinalAnswer: ...

    class FlowAgent(Agent):
        consumes = [Consume(Goal), Consume(PlanStep), Consume(StepResult)]
        produces = Flow().produces()

`step_type` and `result_type` must each declare `goal_field`/`index_field`
(defaults: `"goal"` / `"index"`, matching the ported example's own field
names) — give them defaults (e.g. `index: int = 0`, `goal: str = ""`) so
`plan()`/`execute_step()` don't need to fill them in; the recipe stamps the
real values on every create and overwrites whatever you put there.
`final_type` is stamped with `goal_field` too, if it declares one (useful
to tell several goals' final artifacts apart in one `Context`, as
`test_recipe_plan_execute.py`'s concurrent-goals test does) — optional,
`finish()` isn't required to declare that field.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ..artifacts import Artifact
from ..context import Context
from ..produce import Produce, ProduceCall

GoalT = TypeVar("GoalT", bound=BaseModel)
StepT = TypeVar("StepT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
FinalT = TypeVar("FinalT", bound=BaseModel)


class PlanExecute(Generic[GoalT, StepT, ResultT, FinalT], ABC):
    """Subclass with the three domain hooks; `produces()` gives the three
    `Produce`s to wire into an `Agent` (see module docstring)."""

    goal_type: type[GoalT]
    step_type: type[StepT]
    result_type: type[ResultT]
    final_type: type[FinalT]
    goal_field: str = "goal"
    index_field: str = "index"

    @abstractmethod
    async def plan(self, context: Context, goal: Artifact[GoalT]) -> list[StepT]:
        """The ordered steps for `goal` (called once per goal, §42)."""

    @abstractmethod
    async def execute_step(
        self,
        context: Context,
        step: Artifact[StepT],
        history: list[Artifact[ResultT]],
    ) -> ResultT:
        """Execute exactly one step; `history` holds every prior step's
        result for this goal, in order (§24: one step per generation)."""

    @abstractmethod
    async def finish(
        self,
        context: Context,
        goal: Artifact[GoalT] | None,
        results: list[Artifact[ResultT]],
    ) -> FinalT:
        """Combine every step's result (in order) into the final artifact."""

    def produces(self) -> list[Produce[Any]]:
        return [_Planner(self), _Executor(self), _Finisher(self)]

    def _step_id(self, goal_id: str, index: int) -> str:
        return f"step:{goal_id}:{index}"

    def _result_id(self, step_id: str) -> str:
        return f"result:{step_id}"

    def _final_id(self, goal_id: str) -> str:
        return f"final:{goal_id}"

    def _ordered_steps(self, context: Context, goal_id: str) -> list[Artifact[StepT]]:
        steps = [
            s
            for s in context.list_artifacts(self.step_type)
            if getattr(s.data, self.goal_field, None) == goal_id
        ]
        return sorted(steps, key=lambda s: getattr(s.data, self.index_field))

    def _goal_id_of(self, artifact: Artifact[Any]) -> str | None:
        value = getattr(artifact.data, self.goal_field, None)
        return value if isinstance(value, str) else None


class _Planner(Produce[Any]):
    def __init__(self, owner: PlanExecute[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.step_type)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        owner = self.owner
        goal = call.trigger
        if goal is None or not isinstance(goal.data, owner.goal_type):
            return None
        if owner._ordered_steps(context, goal.id):
            return None  # already planned (§42)
        steps = await owner.plan(context, goal)
        for index, step_data in enumerate(steps):
            stamped = step_data.model_copy(
                update={owner.goal_field: goal.id, owner.index_field: index}
            )
            handle = self.effects.create(stamped, id=owner._step_id(goal.id, index))
            handle.link("from_goal", goal.id)
        return None


class _Executor(Produce[Any]):
    def __init__(self, owner: PlanExecute[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.result_type)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        owner = self.owner
        artifact = call.trigger
        if artifact is None:
            return None
        goal_id = owner._goal_id_of(artifact)
        if goal_id is None:
            return None
        steps = owner._ordered_steps(context, goal_id)
        history: list[Artifact[Any]] = []
        for step in steps:
            result_id = owner._result_id(step.id)
            existing = context.get(result_id)
            if existing is not None:
                history.append(existing)
                continue  # already executed
            result_data = await owner.execute_step(context, step, history)
            stamped = result_data.model_copy(
                update={
                    owner.goal_field: goal_id,
                    owner.index_field: getattr(step.data, owner.index_field),
                }
            )
            handle = self.effects.create(stamped, id=result_id)
            handle.link("executes", step)
            return None  # one step per generation; the new result re-triggers this
        return None


class _Finisher(Produce[Any]):
    def __init__(self, owner: PlanExecute[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.final_type)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        owner = self.owner
        artifact = call.trigger
        if artifact is None:
            return None
        goal_id = owner._goal_id_of(artifact)
        if goal_id is None:
            return None
        if context.get(owner._final_id(goal_id)) is not None:
            return None  # already finished
        steps = owner._ordered_steps(context, goal_id)
        if not steps:
            return None
        results: list[Artifact[Any]] = []
        for step in steps:
            result = context.get(owner._result_id(step.id))
            if result is None:
                return None  # wait for every step's result (§24, §69)
            results.append(result)
        goal = context.get(goal_id)
        final_data = await owner.finish(context, goal, results)
        if hasattr(final_data, owner.goal_field):
            final_data = final_data.model_copy(update={owner.goal_field: goal_id})
        handle = self.effects.create(final_data, id=owner._final_id(goal_id))
        for result in results:
            handle.link("supported_by", result)
        return None
