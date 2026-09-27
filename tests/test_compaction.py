"""Context compaction: squash old commit history into a baseline snapshot and
trim per-artifact history, without changing `version`/`head_id`/`context_hash`."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Budget,
    Consume,
    Context,
    Produce,
    ProduceCall,
    Runtime,
)
from reactifact.audit import context_hash


class Number(BaseModel):
    value: int


class Increment(Produce[Number]):
    """Updates one stable artifact each run, so commits and history both grow."""

    artifact_type = Number

    async def produce(self, call: ProduceCall) -> None:
        trigger = call.trigger
        if trigger is None:
            return None
        call.effects.create(Number(value=trigger.data.value + 1), id="counter")


class IncrementAgent(Agent):
    name = "increment"
    consumes = [Consume(Number)]
    produces = [Increment()]


def _built(max_runs: int = 6) -> Context:
    ctx = Context()
    runtime = Runtime(ctx, agents=[IncrementAgent()], budget=Budget(max_runs=max_runs))
    ctx.create(Number(value=0))
    asyncio.run(runtime.arun())
    return ctx


def test_compact_preserves_version_head_and_hash():
    ctx = _built()
    version, head, digest = ctx.version, ctx.head_id, context_hash(ctx)
    assert version >= 6

    dropped = ctx.compact(keep_commits=2)

    assert dropped == version - 2
    assert ctx.version == version  # absolute version is preserved
    assert ctx.head_id == head
    assert context_hash(ctx) == digest  # history, not state — hash unchanged
    assert ctx.compacted_at == version - 2


def test_diff_at_the_baseline_still_matches_pre_compaction():
    ctx = _built()
    baseline = ctx.version - 2
    expected = ctx.diff(baseline, ctx.version)

    ctx.compact(keep_commits=2)

    assert ctx.diff(baseline, ctx.version) == expected


def test_dropped_commits_are_gone_but_replay_at_head_is_intact():
    ctx = _built()
    state_at_head = ctx._log.replay_state(ctx.version)

    ctx.compact(keep_commits=1)

    assert ctx.diff(ctx.compacted_at, ctx.version)  # replayable from the baseline
    assert ctx._log.replay_state(ctx.version) == state_at_head


def test_checkout_below_the_baseline_is_refused():
    ctx = _built()
    ctx.compact(keep_commits=2)

    with pytest.raises(ValueError, match="compaction baseline"):
        ctx.checkout(ctx.compacted_at - 1)

    ctx.checkout(ctx.compacted_at)  # down to the baseline is allowed
    assert ctx.version == ctx.compacted_at


def test_diff_below_the_baseline_is_refused():
    ctx = _built()
    ctx.compact(keep_commits=2)

    with pytest.raises(ValueError, match="compaction baseline"):
        ctx.diff(0, ctx.version)


def test_persistence_roundtrip_preserves_the_baseline():
    ctx = _built()
    baseline = ctx.version - 2
    ctx.compact(keep_commits=2)
    digest = context_hash(ctx)

    restored = Context.from_dict(ctx.to_dict())

    assert restored.version == ctx.version
    assert restored.head_id == ctx.head_id
    assert restored.compacted_at == baseline
    assert context_hash(restored) == digest
    assert restored.snapshot() == ctx.snapshot()
    assert restored.diff(baseline, restored.version) == ctx.diff(baseline, ctx.version)


def test_keep_versions_trims_history_without_changing_version_or_hash():
    ctx = _built()
    counter = ctx.get("counter")
    assert counter is not None
    assert counter.version > 1  # updated several times
    before = counter.version
    digest = context_hash(ctx)

    ctx.compact(keep_commits=ctx.version, keep_versions=1)

    counter = ctx.get("counter")
    assert counter is not None
    assert counter.version == before  # absolute version unchanged
    assert len(counter.history) == 1  # history trimmed
    assert context_hash(ctx) == digest


def test_compaction_is_idempotent():
    ctx = _built()
    first = ctx.compact(keep_commits=3)
    assert first > 0
    assert ctx.compact(keep_commits=3) == 0  # nothing new below the cutoff


def test_clone_copies_the_baseline():
    ctx = _built()
    ctx.compact(keep_commits=2)

    clone = ctx.clone()

    assert clone.compacted_at == ctx.compacted_at
    assert clone.version == ctx.version
    assert context_hash(clone) == context_hash(ctx)
