"""Pins the execution contract documented in docs/en/scheduler-semantics.md.

These are intentionally narrow assertions on the load-bearing claims: ordering,
snapshot/barrier behavior, no-op/idempotent state transitions, and quiescence.
Broader scenarios live in test_concurrency.py / test_budget.py / test_debounce.py;
this file exists so the *specification* cannot silently drift from the code.
"""

import asyncio

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Runtime
from reactifact.budget import RunOutcome
from reactifact.events import EventType


class Item(BaseModel):
    value: int


class Other(BaseModel):
    text: str


# --- ordering: commit order is work order, not completion order ------------- #


def test_commit_order_is_work_order_under_parallel_dispatch():
    """Lower `priority` commits first even when it finishes last."""

    class Slow(Agent):
        name = "slow"
        consumes = [Consume(Item)]
        priority = 0

        async def run(self, event, context):
            await asyncio.sleep(0.05)  # finishes after Fast
            return Patch().create(Other(text="slow"))

    class Fast(Agent):
        name = "fast"
        consumes = [Consume(Item)]
        priority = 1

        async def run(self, event, context):
            return Patch().create(Other(text="fast"))

    ctx = Context()
    runtime = Runtime(ctx, agents=[Fast(), Slow()], max_concurrency=2)
    ctx.create(Item(value=1))
    asyncio.run(runtime.arun())

    # results are gathered in work order, so commits land slow -> fast
    assert [commit.author for commit in ctx.history()] == ["slow", "fast"]


# --- atomic/idempotent state transitions ------------------------------------ #


def test_noop_update_emits_no_event_and_does_not_bump_version():
    ctx = Context()
    artifact = ctx.create(Item(value=1))
    ctx.drain_events()  # drop the creation event

    same = ctx.update(artifact.id, Item(value=1))  # identical data

    assert same is not None
    assert same.version == artifact.version  # no new version
    assert ctx.drain_events() == []  # no ARTIFACT_UPDATED, no cascade


def test_events_are_derived_from_commits_in_fifo_order():
    ctx = Context()
    ctx.create(Item(value=1))
    ctx.drain_events()
    artifact = ctx.create(Item(value=2))
    ctx.drain_events()

    ctx.update(artifact.id, Item(value=3))
    ctx.delete(artifact.id)

    assert [e.type for e in ctx.drain_events()] == [
        EventType.ARTIFACT_UPDATED,
        EventType.ARTIFACT_DELETED,
    ]


# --- quiescence ------------------------------------------------------------- #


def test_no_matching_agent_is_quiescence_zero_runs_and_completed():
    class ConsumesOther(Agent):
        consumes = [Consume(Other)]

        async def run(self, event, context):
            return None

    ctx = Context()
    ctx.create(Item(value=1))  # nothing consumes Item
    runtime = Runtime(ctx, agents=[ConsumesOther()])

    assert asyncio.run(runtime.arun()) == 0
    assert runtime.outcome == RunOutcome.COMPLETED


# --- list order ------------------------------------------------------------- #


def test_list_artifacts_preserves_insertion_order():
    ctx = Context()
    ctx.create(Item(value=1))
    ctx.create(Other(text="a"))
    ctx.create(Item(value=2))

    assert [type(a.data).__name__ for a in ctx.list_artifacts()] == [
        "Item",
        "Other",
        "Item",
    ]
    assert [a.data.value for a in ctx.list_artifacts(Item)] == [1, 2]
