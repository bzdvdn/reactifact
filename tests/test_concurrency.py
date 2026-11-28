import asyncio
import time

import pytest
from pydantic import BaseModel
from reactifact import Agent, Budget, Consume, Context, Patch, Runtime


class Number(BaseModel):
    value: int


def test_priority_orders_sequential_runs():
    order: list[str] = []

    class Alpha(Agent):
        consumes = [Consume(Number)]
        priority = -5

        async def run(self, event, context):
            order.append("alpha")
            return None

    class Omega(Agent):
        consumes = [Consume(Number)]
        priority = 10

        async def run(self, event, context):
            order.append("omega")
            return None

    ctx = Context()
    runtime = Runtime(ctx, agents=[Omega(), Alpha()])  # registered out of order
    ctx.create(Number(value=1))
    asyncio.run(runtime.arun())

    assert order == ["alpha", "omega"]


def test_parallel_fanout_runs_concurrently():
    calls = 0
    lock = asyncio.Lock()

    class Sleepy(Agent):
        consumes = [Consume(Number)]
        priority = 0

        async def run(self, event, context):
            nonlocal calls
            async with lock:
                calls += 1
            await asyncio.sleep(0.06)
            return None

    ctx = Context()
    runtime = Runtime(ctx, agents=[Sleepy(), Sleepy(), Sleepy()], max_concurrency=3)
    ctx.create(Number(value=1))

    start = time.monotonic()
    assert asyncio.run(runtime.arun()) == 3
    elapsed = time.monotonic() - start

    assert calls == 3
    assert elapsed < 0.13  # ~0.06 if parallel; 0.18+ if sequential


def test_parallel_respects_semaphore_cap():
    active = 0
    peak = 0

    class Blocked(Agent):
        consumes = [Consume(Number)]

        async def run(self, event, context):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.03)
            active -= 1
            return None

    ctx = Context()
    runtime = Runtime(ctx, agents=[Blocked()] * 5, max_concurrency=2)
    ctx.create(Number(value=1))

    asyncio.run(runtime.arun())
    assert peak == 2  # the semaphore caps at max_concurrency simultaneously


def test_parallel_snapshot_and_provenance():
    class Double(Agent):
        consumes = [Consume(Number)]
        priority = 0

        async def run(self, event, context):
            number = context.get(event.artifact_id)
            await asyncio.sleep(0.02)
            return Patch().create(Number(value=number.data.value * 2))

    class Triple(Agent):
        consumes = [Consume(Number)]
        priority = 1

        async def run(self, event, context):
            number = context.get(event.artifact_id)
            await asyncio.sleep(0.02)
            return Patch().create(Number(value=number.data.value * 3))

    ctx = Context()
    runtime = Runtime(
        ctx, agents=[Double(), Triple()], max_concurrency=2, budget=Budget(max_runs=2)
    )
    source = ctx.create(Number(value=5))
    asyncio.run(runtime.arun())

    produced = [a.data.value for a in ctx.list_artifacts(Number) if a.id != source.id]
    assert sorted(produced) == [10, 15]  # both read the same v0 snapshot

    # reads relationships are recorded independently for each run
    for commit in ctx.history():
        assert any(r.artifact_id == source.id and r.version == 0 for r in commit.reads)


def test_concurrent_arun_on_same_runtime_raises():
    """Two concurrent arun() calls on one Runtime would race on shared turn
    state (budget/outcome) — the second call is rejected instead."""

    class Slow(Agent):
        consumes = [Consume(Number)]

        async def run(self, event, context):
            await asyncio.sleep(0.05)
            return None

    ctx = Context()
    runtime = Runtime(ctx, agents=[Slow()])
    ctx.create(Number(value=1))

    async def go() -> None:
        first = asyncio.create_task(runtime.arun())
        await asyncio.sleep(0.01)  # let the first call actually start a turn
        with pytest.raises(RuntimeError, match="not reentrant"):
            await runtime.arun()
        await first

    asyncio.run(go())


def test_dispatch_waits_for_slow_siblings_before_raising_on_error():
    """A failing agent in a concurrent generation must not leave its still-
    running siblings orphaned in the background — dispatch waits for all of
    them before the exception propagates."""
    finished: list[str] = []

    class Failing(Agent):
        consumes = [Consume(Number)]

        async def run(self, event, context):
            raise ValueError("boom")

    class Slow(Agent):
        consumes = [Consume(Number)]

        async def run(self, event, context):
            await asyncio.sleep(0.05)
            finished.append("slow")
            return None

    ctx = Context()
    runtime = Runtime(ctx, agents=[Failing(), Slow(), Slow()], max_concurrency=3)
    ctx.create(Number(value=1))

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(runtime.arun())

    assert finished == ["slow", "slow"]


def test_agent_concurrency_limit_capped():
    """Agent.concurrency_limit caps parallel executions independent of the
    runtime max_concurrency (e.g. throttle LLM producers to 2 calls)."""
    import asyncio as _asyncio

    tracker = {"active": 0, "peak": 0}

    class Tiered(Agent):
        concurrency_limit = 2
        consumes = [Consume(Number)]

        async def run(self, event, context):
            tracker["active"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["active"])
            await _asyncio.sleep(0.02)
            tracker["active"] -= 1
            return None

    ctx = Context()
    runtime = Runtime(
        ctx, agents=[Tiered()] * 6
    )  # global cap None — only the tier applies
    for _ in range(6):
        ctx.create(Number(value=_))
    _asyncio.run(runtime.arun())
    assert tracker["peak"] <= 2, tracker
