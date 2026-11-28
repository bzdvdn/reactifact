import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Budget,
    Context,
    EventType,
    Patch,
    RunOutcome,
    Runtime,
    Trigger,
)


class Number(BaseModel):
    value: int


class Ripper(Agent):
    """An agent that chains: each step doubles the value and spawns a new one."""

    def __init__(self):
        super().__init__(
            name="ripper",
            triggers=[Trigger(EventType.ARTIFACT_CREATED, Number)],
        )

    async def run(self, event, context):
        art = context.get(event.artifact_id)
        if art is None:
            return None
        return Patch().create(Number(value=art.data.value * 2))


def test_budget_max_runs_exceeded():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Ripper()], budget=Budget(max_runs=3))
    ctx.create(Number(value=1))

    total = asyncio.run(runtime.arun())

    assert runtime.outcome == RunOutcome.BUDGET_RUNS_EXCEEDED
    assert runtime.last_stats is not None
    assert runtime.last_stats.outcome == RunOutcome.BUDGET_RUNS_EXCEEDED
    assert runtime.last_stats.runs == total == 3
    assert runtime.last_stats.duration >= 0


def test_budget_max_iterations_exhausted():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Ripper()])
    ctx.create(Number(value=1))

    # the diverging chain hits the generation limit and honestly reports it
    asyncio.run(runtime.arun(max_iterations=2))
    assert runtime.outcome == RunOutcome.ITERATIONS_EXHAUSTED
    assert runtime.last_stats.runs == 2


def test_budget_per_turn_reset():
    ctx = Context()
    runtime = Runtime(ctx, agents=[Ripper()], budget=Budget(max_runs=2))

    ctx.create(Number(value=1))
    asyncio.run(runtime.arun())
    assert runtime.last_stats.runs == 2

    # the next turn starts with a fresh budget
    ctx.create(Number(value=4))
    asyncio.run(runtime.arun())
    assert runtime.last_stats.runs == 2
    assert runtime.last_stats.outcome == RunOutcome.BUDGET_RUNS_EXCEEDED


def test_budget_time_exceeded():

    class Slow(Agent):
        def __init__(self):
            super().__init__(
                name="slow", triggers=[Trigger(EventType.ARTIFACT_CREATED, Number)]
            )

        async def run(self, event, context):
            await asyncio.sleep(0.05)
            return None

    ctx = Context()
    runtime = Runtime(ctx, agents=[Slow()], budget=Budget(max_seconds=0.005))
    ctx.create(Number(value=1))

    asyncio.run(runtime.arun())
    assert runtime.outcome == RunOutcome.BUDGET_TIME_EXCEEDED


def test_run_stats_completed():
    class Doubler(Agent):
        def __init__(self):
            super().__init__(
                name="doubler",
                triggers=[Trigger(EventType.ARTIFACT_CREATED, Number)],
            )

        async def run(self, event, context):
            art = context.get(event.artifact_id)
            if art is None:
                return None
            return Patch().update(art.id, Number(value=art.data.value * 2))

    ctx = Context()
    runtime = Runtime(ctx, agents=[Doubler()])
    number = ctx.create(Number(value=5))

    assert asyncio.run(runtime.arun()) == 1
    assert runtime.outcome == RunOutcome.COMPLETED
    assert runtime.last_stats.runs == 1
    assert runtime.last_stats.outcome == RunOutcome.COMPLETED
    assert ctx.get(number.id).data.value == 10
