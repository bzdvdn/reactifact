"""Outbox (§42/§55): committed side-effect intents dispatched after commit.

A produce records `PendingAction`s instead of doing I/O; the runtime dispatches
them once per stable id, so replay/retry/merge cannot re-send.
"""

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    MergeConflict,
    PendingAction,
    Runtime,
    RuntimeResources,
)
from reactifact.effects import Effects, current_effects
from reactifact.replay import replay_summary


class Doc(BaseModel):
    text: str


class Notifier(Agent):
    consumes = [Consume(Doc)]

    async def run(self, event, context):
        current_effects().act(
            "notify", key="order:1", payload={"to": "ops@example.com"}
        )
        return None


class TwinNotifier(Agent):
    consumes = [Consume(Doc)]

    async def run(self, event, context):
        current_effects().act(
            "notify", key="order:1", payload={"to": "ops@example.com"}
        )
        return None


def _recording_dispatcher(calls: list[str]):
    async def dispatcher(context, action):
        calls.append(action.data.idempotency_key)

    return dispatcher


def test_act_records_a_pending_action_and_is_idempotent():
    ctx = Context(resources=RuntimeResources())
    effects = Effects(ctx)
    handle = effects.act("notify", key="order:1", payload={"to": "x"})
    assert handle is not None
    assert handle.id == "action:order:1"

    ctx.create(
        PendingAction(kind="notify", idempotency_key="order:1"), id="action:order:1"
    )
    assert effects.act("notify", key="order:1") is None


def test_dispatcher_runs_once_and_marks_dispatched():
    calls: list[str] = []
    ctx = Context(resources=RuntimeResources())
    runtime = Runtime(ctx, agents=[Notifier()], dispatcher=_recording_dispatcher(calls))
    ctx.create(Doc(text="trigger"), id="doc:1")
    asyncio.run(runtime.arun())

    assert calls == ["order:1"]
    action = ctx.get("action:order:1")
    assert action is not None
    assert action.data.status == "dispatched"
    assert action.data.dispatched_at is not None


def test_same_generation_duplicates_dispatch_once():
    """Snapshot isolation means two produces in one generation both create the
    intent; the outbox dedupes at drain, so the side effect fires once."""
    calls: list[str] = []
    ctx = Context(resources=RuntimeResources())
    runtime = Runtime(
        ctx,
        agents=[Notifier(), TwinNotifier()],
        dispatcher=_recording_dispatcher(calls),
    )
    ctx.create(Doc(text="trigger"), id="doc:1")
    asyncio.run(runtime.arun())

    assert calls == ["order:1"]
    assert len(ctx.list_artifacts(PendingAction)) == 1


def test_retry_does_not_resend():
    calls: list[str] = []
    ctx = Context(resources=RuntimeResources())
    runtime = Runtime(ctx, agents=[Notifier()], dispatcher=_recording_dispatcher(calls))
    ctx.create(Doc(text="one"), id="doc:1")
    asyncio.run(runtime.arun())
    assert calls == ["order:1"]

    # a second trigger re-runs the produce; the stable id means no new intent
    ctx.create(Doc(text="two"), id="doc:2")
    asyncio.run(runtime.arun())
    assert calls == ["order:1"]


def test_dispatcher_failure_is_recorded_and_raises():
    async def failing(context, action):
        raise RuntimeError("smtp down")

    seen: list[tuple[str, str]] = []

    def on_error(action, exc):
        seen.append((action.id, str(exc)))

    ctx = Context(resources=RuntimeResources())
    runtime = Runtime(
        ctx, agents=[Notifier()], dispatcher=failing, on_dispatch_error=on_error
    )
    ctx.create(Doc(text="trigger"), id="doc:1")
    with pytest.raises(RuntimeError, match="smtp down"):
        asyncio.run(runtime.arun())

    action = ctx.get("action:order:1")
    assert action is not None
    assert action.data.status == "failed"
    assert action.data.error is not None
    assert action.data.attempts == 1
    assert seen == [("action:order:1", "smtp down")]


def test_retry_action_resets_attempts():
    ctx = Context(resources=RuntimeResources())
    ctx.create(
        PendingAction(kind="notify", idempotency_key="k", payload={"to": "x"}),
        id="action:k",
    )
    ctx.mark_failed("action:k", error="smtp down")
    failed = ctx.get("action:k")
    assert failed is not None and failed.data.attempts == 1

    ctx.retry_action("action:k")
    rearmed = ctx.get("action:k")
    assert rearmed is not None
    assert rearmed.data.status == "pending"
    assert rearmed.data.attempts == 0
    assert rearmed.data.error is None


def test_merge_converges_on_one_action_and_flush_dispatches_it():
    calls: list[str] = []
    parent = Context(resources=RuntimeResources())
    left = parent.branch(name="left")
    right = parent.branch(name="right")
    data = PendingAction(kind="notify", idempotency_key="k", payload={"to": "x"})
    left.create(data, id="action:k")
    right.create(data.model_copy(deep=True), id="action:k")

    parent.merge(left)
    parent.merge(right)  # equal data → no-op, still one action
    assert len(parent.list_artifacts(PendingAction)) == 1
    assert calls == []  # merge is pure state; it never dispatches

    runtime = Runtime(parent, dispatcher=_recording_dispatcher(calls))
    assert asyncio.run(runtime.flush_pending_actions()) == 1
    assert calls == ["k"]
    assert asyncio.run(runtime.flush_pending_actions()) == 0


def test_merge_of_divergent_actions_conflicts():
    parent = Context(resources=RuntimeResources())
    left = parent.branch(name="left")
    right = parent.branch(name="right")
    left.create(
        PendingAction(kind="notify", idempotency_key="k", payload={"to": "a"}),
        id="action:k",
    )
    right.create(
        PendingAction(kind="notify", idempotency_key="k", payload={"to": "b"}),
        id="action:k",
    )
    parent.merge(left)
    with pytest.raises(MergeConflict):
        parent.merge(right)


def test_merge_converges_when_one_branch_already_dispatched():
    """Dispatch bookkeeping (status) is not part of the intent: the same action
    recorded on two branches must merge even if one side already sent it."""
    parent = Context(resources=RuntimeResources())
    left = parent.branch(name="left")
    right = parent.branch(name="right")
    data = PendingAction(kind="notify", idempotency_key="k", payload={"to": "x"})
    left.create(data, id="action:k")
    right.create(data.model_copy(deep=True), id="action:k")
    left.mark_dispatched("action:k")

    parent.merge(left)
    parent.merge(right)  # no MergeConflict — same intent
    assert len(parent.list_artifacts(PendingAction)) == 1


def test_merge_prefers_a_dispatched_side_regardless_of_direction():
    """A merge must not resurrect an already-sent action into `pending` — the
    `dispatched` side wins whichever branch is the merge target."""
    data = PendingAction(kind="notify", idempotency_key="k", payload={"to": "x"})

    # target pending, dispatched merged in
    parent = Context(resources=RuntimeResources())
    target = parent.branch(name="t")
    other = parent.branch(name="o")
    target.create(data, id="action:k")
    other.create(data.model_copy(deep=True), id="action:k")
    other.mark_dispatched("action:k")
    target.merge(other)
    assert target.get("action:k").data.status == "dispatched"
    assert target.pending_actions() == []

    # target dispatched, pending merged in
    parent2 = Context(resources=RuntimeResources())
    target2 = parent2.branch(name="t")
    other2 = parent2.branch(name="o")
    target2.create(data.model_copy(deep=True), id="action:k")
    other2.create(data.model_copy(deep=True), id="action:k")
    target2.mark_dispatched("action:k")
    target2.merge(other2)
    assert target2.get("action:k").data.status == "dispatched"
    assert target2.pending_actions() == []


def test_pending_action_without_dispatcher_warns(capsys):
    ctx = Context(resources=RuntimeResources())
    runtime = Runtime(ctx, agents=[Notifier()])  # no dispatcher configured
    ctx.create(Doc(text="trigger"), id="doc:1")
    asyncio.run(runtime.arun())

    assert "no dispatcher=" in capsys.readouterr().err
    assert len(ctx.pending_actions()) == 1


def test_replay_summary_reports_action_states():
    calls: list[str] = []
    ctx = Context(resources=RuntimeResources())
    runtime = Runtime(ctx, agents=[Notifier()], dispatcher=_recording_dispatcher(calls))
    ctx.create(Doc(text="trigger"), id="doc:1")
    asyncio.run(runtime.arun())

    summary = replay_summary(ctx)
    assert summary["dispatched_actions"] == 1
    assert summary["pending_actions"] == 0
