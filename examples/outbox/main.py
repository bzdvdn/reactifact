"""outbox — one intent, one send, even across retries, replays and merges.

No LLM anywhere: this is deterministic by design (§67), so the claim is a count,
not a vibe. The produce records a `PendingAction`; `Runtime(dispatcher=...)`
performs it once after the commit. The demo walks the seven cases that make the
split worth having:

  1. commit -> dispatch        the produce does no I/O; the dispatcher sends once
  2. re-derivation             a later edit re-runs the produce; stable key -> no resend
  3. same generation           two producers, one event -> the outbox dedupes
  4. two merged branches       the same intent on both forks -> one delivery
 5. replay                    state is reconstructed; nothing is re-sent
 6. failure -> retry          a failed dispatch is state, not a lost effect
 7. app-owned retry           backoff lives in the app, not the framework

Run:  .venv/bin/python -m examples.outbox.main
"""

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script — add repo root to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reactifact import (
    Context,
    PendingAction,
    Runtime,
    RuntimeResources,
    SessionStore,
)
from reactifact.checkpoints import InMemoryKVBackend
from reactifact.replay import replay_context, replay_summary

from examples.outbox.agents import AGENTS, DUPLICATE_AGENTS
from examples.outbox.dispatcher import (
    SentLog,
    make_dispatcher,
    make_flaky,
    with_retries,
)
from examples.outbox.models import Order

ACTION = "action:notify:42"


def _order(order_id: str, total: float = 125.0) -> Order:
    return Order(id=order_id, customer="ada@example.com", total=total)


async def happy_path() -> tuple[Context, SentLog]:
    """1. The produce records the intent; the runtime dispatches it once."""
    log = SentLog()
    context = Context(resources=RuntimeResources())
    context.create(_order("42"), id="order:42")
    dispatch, _ = make_dispatcher(log)
    await Runtime(context, agents=AGENTS, dispatcher=dispatch).arun()
    return context, log


async def rederivation() -> tuple[int, int]:
    """2. A later edit re-runs the produce — but the key is stable, so no resend."""
    context, first = await happy_path()
    context.update("order:42", _order("42", total=130.0))
    second = SentLog()
    dispatch, _ = make_dispatcher(second)
    await Runtime(context, agents=AGENTS, dispatcher=dispatch).arun()
    return len(first), len(second)


async def same_generation() -> tuple[Context, SentLog]:
    """3. Two producers react to one event (snapshot isolation); drain dedupes."""
    log = SentLog()
    context = Context(resources=RuntimeResources())
    context.create(_order("7"), id="order:7")
    dispatch, _ = make_dispatcher(log)
    await Runtime(context, agents=DUPLICATE_AGENTS, dispatcher=dispatch).arun()
    return context, log


async def branches() -> tuple[Context, SentLog, int]:
    """4. The same intent on two forks — merge is clean, delivery happens once."""
    log = SentLog()
    dispatch, _ = make_dispatcher(log)  # the external system dedupes by key
    parent = Context(resources=RuntimeResources())
    left = parent.branch(name="left")
    right = parent.branch(name="right")
    # the trigger lives *on* each fork (a fork clones state, not the event queue)
    left.create(_order("9"), id="order:9")
    right.create(_order("9"), id="order:9")
    await Runtime(left, agents=AGENTS, dispatcher=dispatch).arun()
    await Runtime(right, agents=AGENTS, dispatcher=dispatch).arun()
    left.merge(right)  # same intent -> no MergeConflict (bookkeeping ignored)
    pending = await Runtime(left, dispatcher=dispatch).flush_pending_actions()
    return left, log, pending


async def replay() -> tuple[int, int, dict[str, object]]:
    """5. Replay reconstructs the state at a commit and never re-sends."""
    log = SentLog()
    store = SessionStore(InMemoryKVBackend())
    session = await store.open("orders", resources=RuntimeResources())
    session.context.create(_order("100"), id="order:100")
    dispatch, _ = make_dispatcher(log)
    runtime = Runtime(
        session.context, agents=AGENTS, session=session, dispatcher=dispatch
    )
    await runtime.arun()
    sent_before = len(log)
    replayed = await replay_context(store, "orders")
    return sent_before, len(log), replay_summary(replayed)


async def failure_retry() -> tuple[Context, SentLog, str, str | None]:
    """6. A failing dispatcher is recorded as `failed`; retry re-arms it."""
    log = SentLog()
    context = Context(resources=RuntimeResources())
    context.create(_order("55"), id="order:55")
    dispatch, _ = make_dispatcher(log, fail_first=True)
    runtime = Runtime(context, agents=AGENTS, dispatcher=dispatch)
    raised = ""
    try:
        await runtime.arun()
    except RuntimeError as exc:
        raised = str(exc)
    action = context.get("action:notify:55")
    failed = action.data.status if action is not None else None
    context.retry_action("action:notify:55")
    await runtime.flush_pending_actions()
    return context, log, raised, failed


async def app_owned_retry() -> tuple[Context, SentLog, str | None, int]:
    """7. Backoff/retry is the app's: a wrapper absorbs a transient outage."""
    log = SentLog()
    base, _ = make_dispatcher(log)
    wrapped = with_retries(make_flaky(base, fail_times=2), attempts=3, delay=0.0)
    context = Context(resources=RuntimeResources())
    context.create(_order("77"), id="order:77")
    await Runtime(context, agents=AGENTS, dispatcher=wrapped).arun()
    action = context.get("action:notify:77")
    status = action.data.status if action is not None else None
    attempts = action.data.attempts if action is not None else -1
    return context, log, status, attempts


async def main() -> None:
    print("outbox — one intent, one send, even across retries, replays and merges\n")

    context, log = await happy_path()
    action = context.get(ACTION)
    assert action is not None and action.data.status == "dispatched"
    assert list(log.keys()) == ["notify:42"]
    print("1. commit -> dispatch")
    print("   produce wrote a PendingAction; the dispatcher sent after the commit.")
    print(f"   sent={log.keys()} action.status={action.data.status}")
    print("   [confirmed] the produce did no I/O itself; exactly one send.\n")

    first, second = await rederivation()
    assert first == 1 and second == 0
    print("2. re-derivation (a later edit re-runs the produce)")
    print(f"   first run sent {first}; the re-run sent {second}.")
    print("   [confirmed] stable key -> the re-run created no new intent.\n")

    context, log = await same_generation()
    assert len(log) == 1 and len(context.list_artifacts(PendingAction)) == 1
    print("3. same generation (two producers, one event)")
    print(
        f"   producers=2 intents={len(context.list_artifacts(PendingAction))} "
        f"sent={len(log)}"
    )
    print("   [confirmed] the outbox deduped at drain; one delivery.\n")

    merged, log, pending = await branches()
    assert len(log) == 1 and pending == 0
    assert len(merged.list_artifacts(PendingAction)) == 1
    print("4. two merged branches (the same intent on both forks)")
    print(
        f"   branches=2 intents-after-merge=1 sent={len(log)} pending-after-flush={pending}"
    )
    print(
        "   [confirmed] merge ignored dispatch bookkeeping; external key gave one send.\n"
    )

    sent_before, sent_after, summary = await replay()
    assert sent_before == 1 and sent_after == sent_before
    assert summary["dispatched_actions"] == 1 and summary["pending_actions"] == 0
    print("5. replay (reconstruct state, do not act again)")
    print(f"   sent before replay={sent_before} after replay={sent_after}")
    print(
        f"   replayed state: dispatched={summary['dispatched_actions']} "
        f"pending={summary['pending_actions']}"
    )
    print("   [confirmed] replay rebuilt the answer without re-sending anything.\n")

    context, log, raised, failed = await failure_retry()
    action = context.get("action:notify:55")
    assert failed == "failed" and len(log) == 1
    assert action is not None and action.data.status == "dispatched"
    assert "smtp" in raised
    print("6. failure -> retry (a failed dispatch is state, not a lost effect)")
    print(f"   first dispatch raised {raised!r}; status was {failed!r}")
    print(
        f"   after retry_action + flush: status={action.data.status} sent={log.keys()}"
    )
    print(
        "   [confirmed] the failure stayed visible and retry delivered exactly once.\n"
    )

    context, log, status, attempts = await app_owned_retry()
    assert status == "dispatched" and attempts == 0 and log.keys() == ["notify:77"]
    print("7. app-owned retry (a wrapper around the dispatcher)")
    print(
        f"   a transient outage absorbed by the wrapper; status={status} "
        f"attempts={attempts} sent={log.keys()}"
    )
    print(
        "   [confirmed] backoff/retry lived in the app; the runtime saw one clean"
        " outcome.\n"
    )

    print("The produce never touched the network: state committed first, the")
    print("side effect ran after — so replay, retries and merged branches can")
    print("reconstruct the decision without repeating the action.")


if __name__ == "__main__":
    asyncio.run(main())
