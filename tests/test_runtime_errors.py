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


class Done(BaseModel):
    pass


_attempts = {"n": 0}


class Flaky(Produce[Done]):
    artifact_type = Done

    async def produce(self, call: ProduceCall):
        _attempts["n"] += 1
        if _attempts["n"] == 1:
            raise RuntimeError("transient failure")
        self.effects.create(Done())


class FlakyAgent(Agent):
    name = "flaky"
    consumes = [Consume(Trigger)]
    produces = [Flaky()]


def test_failed_generation_keeps_its_trigger_for_retry():
    """A raising produce must not lose the event that woke it.

    Before the fix `arun_once` drained the queue first, so the trigger was
    gone after the exception and the retry saw an empty queue: the work was
    silently dropped (zero runs, no artifact). Now the batch is only consumed
    after the generation commits, so a retry re-runs it.
    """
    _attempts["n"] = 0
    ctx = Context()
    runtime = Runtime(ctx, agents=[FlakyAgent()])
    ctx.create(Trigger())

    try:
        asyncio.run(runtime.arun())
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the first attempt to raise")

    assert ctx.latest(Done) is None
    assert len(ctx.pending_events()) == 1  # trigger still queued

    asyncio.run(runtime.arun())
    assert ctx.latest(Done) is not None
    assert _attempts["n"] == 2


def test_arun_once_consumes_trigger_but_keeps_derived_event():
    """One generation: the trigger is consumed, its output's event is not."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[SurviveAgent()])
    ctx.create(Trigger())

    runs = asyncio.run(runtime.arun_once())

    assert runs == 1
    pending = ctx.pending_events()
    assert len(pending) == 1
    assert pending[0].artifact_type is Ok


def test_consume_events_keeps_events_created_during_the_generation():
    """Consuming the trigger batch must preserve the commits' own events.

    `pending_events()` is a peek; `consume_events()` removes by identity, so
    the next generation's triggers (emitted when this generation's patch is
    applied) remain queued.
    """
    ctx = Context()
    trigger = ctx.create(Trigger())
    batch = ctx.pending_events()
    assert len(batch) == 1

    created = ctx.create(Done())
    ctx.consume_events(batch)

    pending = ctx.pending_events()
    assert len(pending) == 1
    assert pending[0].artifact_id == created.id
    assert trigger.id not in {event.artifact_id for event in pending}
