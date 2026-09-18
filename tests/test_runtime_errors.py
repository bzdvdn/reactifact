"""Runtime error handling: agent exceptions must surface through astream()."""

import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Produce, ProduceCall, Runtime


class Trigger(BaseModel):
    pass


class Explode(Produce[Trigger]):
    artifact_type = Trigger

    async def produce(self, call: ProduceCall) -> Patch | None:
        raise RuntimeError("boom inside the agent")


class ExplodeAgent(Agent):
    name = "explode"
    consumes = [Consume(Trigger)]
    produces = [Explode()]


def test_astream_propagates_agent_error():
    ctx = Context()
    runtime = Runtime(ctx, agents=[ExplodeAgent()])
    ctx.create(Trigger())

    async def collect():
        return [ev async for ev in runtime.astream()]

    try:
        asyncio.run(collect())
    except RuntimeError as exc:
        assert "boom inside the agent" in str(exc)
    else:
        raise AssertionError("expected the agent's RuntimeError to propagate")


def test_arun_propagates_agent_error():
    ctx = Context()
    runtime = Runtime(ctx, agents=[ExplodeAgent()])
    ctx.create(Trigger())
    try:
        asyncio.run(runtime.arun())
    except RuntimeError as exc:
        assert "boom inside the agent" in str(exc)
    else:
        raise AssertionError("expected the agent's RuntimeError to propagate")


class Ok(BaseModel):
    n: int = 1


class Survive(Produce[Ok]):
    artifact_type = Ok

    async def produce(self, call: ProduceCall):
        if call.context.get("ok") is not None:
            return None
        self.effects.create(Ok(), id="ok")
        return None


class SurviveAgent(Agent):
    name = "survive"
    consumes = [Consume(Trigger)]
    produces = [Survive()]


def test_isolate_errors_lets_other_agents_finish():
    """With isolate_errors=True, one agent's exception does not stop the run
    or block unrelated agents from making progress in the same generation."""
    ctx = Context()
    caught: list[Exception] = []
    runtime = Runtime(
        ctx,
        agents=[ExplodeAgent(), SurviveAgent()],
        isolate_errors=True,
        on_agent_error=lambda agent, event, exc: caught.append(exc),
    )
    ctx.create(Trigger())
    runs = asyncio.run(runtime.arun())

    assert runs >= 1
    assert ctx.get("ok") is not None
    assert len(caught) == 1
    assert "boom inside the agent" in str(caught[0])
    assert runtime.last_stats is not None
    assert runtime.last_stats.errors == 1


def test_isolate_errors_default_still_propagates():
    """isolate_errors defaults to False: existing fail-loud behavior is unchanged."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[ExplodeAgent()])
    ctx.create(Trigger())
    try:
        asyncio.run(runtime.arun())
    except RuntimeError:
        pass
    else:
        raise AssertionError(
            "expected the agent's RuntimeError to propagate by default"
        )
