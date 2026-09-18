"""plan_execute demo: planner, sequential executor, and finisher produces."""

from __future__ import annotations

from reactifact import Produce, ProduceCall
from reactifact.structured import structured_llm

from .models import FinalAnswer, Goal, PlanBody, PlanStep, StepResult, _Text
from .prompts import EXECUTE, FINISH, MAX_STEPS, PLAN, fallback_plan


class Planner(Produce[PlanStep]):
    # Flow also consumes PlanStep/StepResult — reacts_to keeps Planner from
    # re-running on those, instead of an `isinstance(goal.data, Goal)` guard;
    # `trigger` gets the resolved Goal directly, guaranteed non-None.
    artifact_type = PlanStep
    reacts_to = (Goal,)

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        goal = call.trigger
        assert goal is not None
        if context.list_artifacts(PlanStep):
            return None  # already planned (§42)
        body = await structured_llm(
            context,
            schema=PlanBody,
            system=PLAN.render(max_steps=MAX_STEPS),
            user=goal.data.text,
        )
        steps = (
            body.steps[:MAX_STEPS]
            if body is not None and body.steps
            else fallback_plan(goal.data.text)
        )
        for index, instruction in enumerate(steps):
            step = self.effects.create(
                PlanStep(goal=goal.id, index=index, instruction=instruction),
                id=f"step:{goal.id}:{index}",
            )
            step.link("from_goal", goal.id)
        return None


class Executor(Produce[StepResult]):
    """Runs exactly one still-pending step per generation (§24), gated on its
    predecessor's result — this is what keeps execution sequential rather
    than a `map_reduce`-style fan-out."""

    artifact_type = StepResult

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        steps = sorted(context.list_artifacts(PlanStep), key=lambda s: s.data.index)
        for step in steps:
            if context.get(f"result:{step.id}") is not None:
                continue  # already executed
            if step.data.index > 0:
                prev = steps[step.data.index - 1]
                if context.get(f"result:{prev.id}") is None:
                    return None  # wait for the predecessor's result (§69)
            history = "\n".join(
                f"{s.data.index}. {s.data.instruction} -> "
                f"{context.get(f'result:{s.id}').data.output}"  # type: ignore[union-attr]
                for s in steps[: step.data.index]
            )
            body = await structured_llm(
                context,
                schema=_Text,
                system=EXECUTE.render(
                    index=step.data.index,
                    goal=step.data.goal,
                    history=history or "(none yet)",
                    instruction=step.data.instruction,
                ),
                user=step.data.instruction,
            )
            output = (
                body.text
                if body is not None
                else f"(offline step {step.data.index}) {step.data.instruction}"
            )
            result = self.effects.create(
                StepResult(
                    goal=step.data.goal,
                    index=step.data.index,
                    instruction=step.data.instruction,
                    output=output,
                ),
                id=f"result:{step.id}",
            )
            result.link("executes", step)
            return None  # one step per generation; the next result re-triggers us
        return None


class Finisher(Produce[FinalAnswer]):
    artifact_type = FinalAnswer

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        if context.list_artifacts(FinalAnswer):
            return None
        steps = sorted(context.list_artifacts(PlanStep), key=lambda s: s.data.index)
        if not steps:
            return None
        results = [context.get(f"result:{s.id}") for s in steps]
        if any(r is None for r in results):
            return None  # wait for every step's result (§24)
        goal_id = steps[0].data.goal
        goal = context.get(goal_id)
        transcript = "\n".join(
            f"{r.data.index}. {r.data.instruction} -> {r.data.output}"  # type: ignore[union-attr]
            for r in results
        )
        body = await structured_llm(
            context,
            schema=_Text,
            system=FINISH.render(
                goal=goal.data.text if goal else goal_id, transcript=transcript
            ),
            user=transcript,
        )
        text = body.text if body is not None else f"(offline finish)\n{transcript}"
        final = self.effects.create(
            FinalAnswer(goal=goal_id, text=text), id=f"final:{goal_id}"
        )
        for r in results:
            final.link("supported_by", r)
        return None
