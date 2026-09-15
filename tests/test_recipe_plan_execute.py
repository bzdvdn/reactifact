import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Runtime
from reactifact.recipes import PlanExecute


class Goal(BaseModel):
    text: str


class PlanStep(BaseModel):
    goal: str = ""
    index: int = 0
    instruction: str = ""


class StepResult(BaseModel):
    goal: str = ""
    index: int = 0
    output: str = ""


class FinalAnswer(BaseModel):
    goal: str = ""
    text: str = ""


class Flow(PlanExecute[Goal, PlanStep, StepResult, FinalAnswer]):
    goal_type = Goal
    step_type = PlanStep
    result_type = StepResult
    final_type = FinalAnswer

    async def plan(self, context, goal):
        return [
            PlanStep(instruction=f"step for: {goal.data.text}"),
            PlanStep(instruction="second step"),
        ]

    async def execute_step(self, context, step, history):
        assert len(history) == step.data.index
        return StepResult(output=f"did: {step.data.instruction}")

    async def finish(self, context, goal, results):
        transcript = " | ".join(r.data.output for r in results)
        return FinalAnswer(text=f"{goal.data.text if goal else '?'}: {transcript}")


def make_runtime(ctx: Context) -> Runtime:
    class FlowAgent(Agent):
        consumes = [Consume(Goal), Consume(PlanStep), Consume(StepResult)]
        produces = Flow().produces()

    return Runtime(ctx, agents=[FlowAgent()])


def test_plan_execute_runs_steps_in_order_and_finishes():
    ctx = Context()
    runtime = make_runtime(ctx)

    goal = ctx.create(Goal(text="build a shed"))
    asyncio.run(runtime.arun())

    steps = ctx.list_artifacts(PlanStep)
    assert [s.data.index for s in sorted(steps, key=lambda s: s.data.index)] == [0, 1]
    assert all(s.data.goal == goal.id for s in steps)

    results = ctx.list_artifacts(StepResult)
    assert len(results) == 2

    finals = ctx.list_artifacts(FinalAnswer)
    assert len(finals) == 1
    assert finals[0].data.text == (
        "build a shed: did: step for: build a shed | did: second step"
    )

    # provenance: final -> supported_by -> both results
    supported = ctx.related(finals[0].id, relation="supported_by")
    assert {s.id for s in supported} == {r.id for r in results}


def test_plan_execute_is_idempotent_on_replayed_goal_event():
    ctx = Context()
    runtime = make_runtime(ctx)

    ctx.create(Goal(text="idempotent goal"))
    asyncio.run(runtime.arun())
    asyncio.run(runtime.arun())  # nothing left to drain, but re-run is safe

    assert len(ctx.list_artifacts(PlanStep)) == 2
    assert len(ctx.list_artifacts(FinalAnswer)) == 1


def test_plan_execute_supports_two_concurrent_goals():
    ctx = Context()
    runtime = make_runtime(ctx)

    goal_a = ctx.create(Goal(text="A"))
    goal_b = ctx.create(Goal(text="B"))
    asyncio.run(runtime.arun())

    finals = {f.data.goal: f for f in ctx.list_artifacts(FinalAnswer)}
    assert set(finals) == {goal_a.id, goal_b.id}
    assert finals[goal_a.id].data.text.startswith("A:")
    assert finals[goal_b.id].data.text.startswith("B:")
