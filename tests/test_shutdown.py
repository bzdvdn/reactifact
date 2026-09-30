"""Hard per-turn deadline, graceful shutdown, and the in-flight registry."""

import asyncio
import contextlib

from pydantic import BaseModel
from reactifact import (
    Agent,
    Budget,
    Consume,
    Context,
    Patch,
    RunOutcome,
    Runtime,
    RuntimeResources,
)
from reactifact.runtime import active_runs, cancel_run


class Question(BaseModel):
    text: str


class Step1(BaseModel):
    text: str


class Step2(BaseModel):
    text: str


def _ctx_with_question() -> Context:
    context = Context(resources=RuntimeResources())
    context.create(Question(text="go"))
    return context


def test_hard_deadline_cancels_a_hung_agent_and_keeps_the_batch():
    started = asyncio.Event()

    class Hang(Agent):
        consumes = [Consume(Question)]
        produces = []

        async def run(self, event, context):
            started.set()
            await asyncio.sleep(30)  # cancelled by the deadline
            return None

    async def scenario():
        context = _ctx_with_question()
        runtime = Runtime(context, agents=[Hang()], budget=Budget(max_seconds=0.1))
        runs = await runtime.arun()
        return context, runtime, runs

    context, runtime, runs = asyncio.run(scenario())
    assert runs == 0
    assert runtime.outcome == RunOutcome.BUDGET_TIME_EXCEEDED
    # The generation never committed, so its trigger batch is still queued — a
    # resume re-dispatches it instead of silently dropping the work.
    assert context.pending_events()


def test_request_stop_ends_at_the_next_boundary():
    holder: dict[str, Runtime] = {}

    class First(Agent):
        consumes = [Consume(Question)]
        produces = []

        async def run(self, event, context):
            holder["runtime"].request_stop()  # stop after this generation
            return Patch().create(Step1(text="one"))

    class Second(Agent):
        consumes = [Consume(Step1)]
        produces = []

        async def run(self, event, context):
            return Patch().create(Step2(text="two"))

    async def scenario():
        context = _ctx_with_question()
        runtime = Runtime(context, agents=[First(), Second()])
        holder["runtime"] = runtime
        await runtime.arun()
        return context, runtime

    context, runtime = asyncio.run(scenario())
    assert runtime.outcome == RunOutcome.STOPPED
    assert context.get  # sanity
    assert context.latest(Step1) is not None  # the in-flight generation landed
    assert context.latest(Step2) is None  # no new generation started


def test_registry_tracks_and_clears_in_flight_runs():
    started = asyncio.Event()

    class Slow(Agent):
        consumes = [Consume(Question)]
        produces = []

        async def run(self, event, context):
            started.set()
            await asyncio.sleep(0.05)
            return None

    async def scenario():
        context = _ctx_with_question()
        runtime = Runtime(context, agents=[Slow()])
        task = asyncio.create_task(runtime.arun())
        await started.wait()
        during = active_runs()
        assert len(during) == 1
        assert during[0].generation >= 1
        await task
        assert active_runs() == []
        assert not runtime.in_flight

    asyncio.run(scenario())


def test_cancel_run_cancels_a_specific_turn():
    started = asyncio.Event()

    class Hang(Agent):
        consumes = [Consume(Question)]
        produces = []

        async def run(self, event, context):
            started.set()
            await asyncio.sleep(30)
            return None

    async def scenario():
        context = _ctx_with_question()
        runtime = Runtime(context, agents=[Hang()])
        task = asyncio.create_task(runtime.arun())
        await started.wait()
        run_id = active_runs()[0].run_id
        assert cancel_run(run_id) is True
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert not runtime.in_flight
        assert active_runs() == []

    asyncio.run(scenario())


def test_ashutdown_waits_for_the_turn_to_finish():
    started = asyncio.Event()

    class Slow(Agent):
        consumes = [Consume(Question)]
        produces = []

        async def run(self, event, context):
            started.set()
            await asyncio.sleep(0.05)
            return Patch().create(Step1(text="done"))

    async def scenario():
        context = _ctx_with_question()
        runtime = Runtime(context, agents=[Slow()])
        task = asyncio.create_task(runtime.arun())
        await started.wait()
        await runtime.ashutdown()
        assert not runtime.in_flight
        assert runtime.outcome == RunOutcome.STOPPED
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return context

    context = asyncio.run(scenario())
    assert context.latest(Step1) is not None


def test_ashutdown_timeout_force_cancels():
    started = asyncio.Event()

    class Hang(Agent):
        consumes = [Consume(Question)]
        produces = []

        async def run(self, event, context):
            started.set()
            await asyncio.sleep(30)
            return None

    async def scenario():
        context = _ctx_with_question()
        runtime = Runtime(context, agents=[Hang()])
        task = asyncio.create_task(runtime.arun())
        await started.wait()
        await runtime.ashutdown(timeout=0.05)
        assert not runtime.in_flight
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
