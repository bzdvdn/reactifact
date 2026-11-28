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

    flow = Agent(
        "plan-execute",
        consumes=[Consume(Goal), Consume(PlanStep), Consume(StepResult)],
        steps=Flow().produces(),
    )

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

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from pydantic import BaseModel

from ..agent import Agent, Emit, Step
from ..artifacts import Artifact
from ..consume import Consume
from ..context import Context
from ..read import Read
from .inputs import correlation_id_of

if TYPE_CHECKING:
    from ..pipeline import Pipeline

GoalT = TypeVar("GoalT", bound=BaseModel)
StepT = TypeVar("StepT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
FinalT = TypeVar("FinalT", bound=BaseModel)


class PlanExecute(Generic[GoalT, StepT, ResultT, FinalT], ABC):
    """Subclass with the three domain hooks; `produces()` gives the three
    `Step`s to wire into a `Agent` (see module docstring)."""

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

    @classmethod
    def from_funcs(
        cls,
        *,
        goal_type: type[GoalT],
        step_type: type[StepT],
        result_type: type[ResultT],
        final_type: type[FinalT],
        plan: Callable[..., Any],
        execute_step: Callable[..., Any],
        finish: Callable[..., Any],
        goal_field: str | None = None,
        index_field: str | None = None,
    ) -> PlanExecute[GoalT, StepT, ResultT, FinalT]:
        """Builds the flow from plain functions instead of a subclass.

        Hooks may be sync or async, mirroring the methods they replace:

        ```python
        flow = PlanExecute.from_funcs(
            goal_type=Goal,
            step_type=PlanStep,
            result_type=StepResult,
            final_type=FinalAnswer,
            plan=plan,
            execute_step=execute_step,
            finish=finish,
        )
        pipeline = Pipeline(steps=flow.produces(), seed_type=Goal)
        ```
        """
        return _FuncPlanExecute(
            goal_type=goal_type,
            step_type=step_type,
            result_type=result_type,
            final_type=final_type,
            plan=plan,
            execute_step=execute_step,
            finish=finish,
            goal_field=goal_field,
            index_field=index_field,
        )

    def produces(self) -> list[Step[Any]]:
        return [_Planner(self), _Executor(self), _Finisher(self)]

    def reads(self) -> list[Consume]:
        """The artifact types the flow reads: goal, step and result."""
        return [
            Consume(self.goal_type),
            Consume(self.step_type),
            Consume(self.result_type),
        ]

    def consumes(self) -> list[Consume]:
        """Deprecated alias for `reads()`."""
        return self.reads()

    def agent(self, name: str = "plan-execute", **kwargs: Any) -> Agent:
        """A ready `Agent` (consumes inferred) for `Runtime(agents=...)`."""
        return Agent(name, consumes=self.reads(), steps=self.produces(), **kwargs)

    #: Compatibility alias for `agent()`.
    group = agent

    def pipeline(self, *, name: str = "plan-execute", **kwargs: Any) -> Pipeline:
        """A `Pipeline` with this flow already grouped and wired.

        ```python
        pipeline = PlanExecute.from_funcs(...).pipeline(seed_type=Goal)
        result = PipelineRunner(pipeline).run(Goal(text="..."))
        ```
        """
        from ..pipeline import Pipeline

        return Pipeline(agents=[self.agent(name)], **kwargs)

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


class _FuncPlanExecute(PlanExecute[GoalT, StepT, ResultT, FinalT]):
    """`PlanExecute.from_funcs` adapter: delegates each hook to a callable."""

    def __init__(
        self,
        *,
        goal_type: type[GoalT],
        step_type: type[StepT],
        result_type: type[ResultT],
        final_type: type[FinalT],
        plan: Callable[..., Any],
        execute_step: Callable[..., Any],
        finish: Callable[..., Any],
        goal_field: str | None,
        index_field: str | None,
    ):
        self.goal_type = goal_type
        self.step_type = step_type
        self.result_type = result_type
        self.final_type = final_type
        self._plan = plan
        self._execute_step = execute_step
        self._finish = finish
        if goal_field is not None:
            self.goal_field = goal_field
        if index_field is not None:
            self.index_field = index_field

    async def plan(self, context: Context, goal: Artifact[GoalT]) -> list[StepT]:
        result = self._plan(context, goal)
        if inspect.isawaitable(result):
            result = await result
        return cast("list[StepT]", list(result))

    async def execute_step(
        self, context: Context, step: Artifact[StepT], history: list[Artifact[ResultT]]
    ) -> ResultT:
        result = self._execute_step(context, step, history)
        if inspect.isawaitable(result):
            result = await result
        return cast("ResultT", result)

    async def finish(
        self,
        context: Context,
        goal: Artifact[GoalT] | None,
        results: list[Artifact[ResultT]],
    ) -> FinalT:
        result = self._finish(context, goal, results)
        if inspect.isawaitable(result):
            result = await result
        return cast("FinalT", result)


class _Planner(Step[Any]):
    def __init__(self, owner: PlanExecute[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.step_type)

    async def run(
        self, context: Context, trigger: Artifact[Any] | None
    ) -> list[Emit] | None:
        owner = self.owner
        goal = trigger
        if goal is None or not isinstance(goal.data, owner.goal_type):
            return None
        # A plan is a set of Step artifacts, not one derived id: Read.idempotent
        # cannot express "no steps for this goal", so the guard stays explicit.
        if owner._ordered_steps(context, goal.id):
            return None  # already planned (§42)
        steps = await owner.plan(context, goal)
        return [
            Emit(
                step_data.model_copy(
                    update={owner.goal_field: goal.id, owner.index_field: index}
                ),
                id=owner._step_id(goal.id, index),
                links=(("from_goal", goal.id),),
            )
            for index, step_data in enumerate(steps)
        ]


class _Executor(Step[Any]):
    # Deliberately imperative: it must wake on both Step and StepResult events
    # (to advance one step per generation), which a single per-step Consume
    # cannot express.
    def __init__(self, owner: PlanExecute[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.result_type)

    async def run(self, context: Context, trigger: Artifact[Any] | None) -> Emit | None:
        owner = self.owner
        artifact = trigger
        if artifact is None:
            return None
        goal_id = correlation_id_of(artifact, owner.goal_field)
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
            return Emit(
                stamped,
                id=result_id,
                links=(("executes", step),),
            )  # one step per generation; the new result re-triggers this
        return None


class _Finisher(Step[Any]):
    def __init__(self, owner: PlanExecute[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(
            artifact_type=owner.final_type,
            reads=(
                Read.idempotent(
                    owner.result_type,
                    id_of=lambda result: owner._final_id(
                        getattr(result.data, owner.goal_field)
                    ),
                ),
            ),
        )

    async def run(self, context: Context, trigger: Artifact[Any] | None) -> Emit | None:
        owner = self.owner
        artifact = trigger
        if artifact is None:
            return None
        goal_id = correlation_id_of(artifact, owner.goal_field)
        if goal_id is None:
            return None
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
        return Emit(
            final_data,
            id=owner._final_id(goal_id),
            links=tuple(("supported_by", result) for result in results),
        )
