# outbox — one intent, one send

A tiny order pipeline that exists to make one point concretely, by count:
**an external side effect is recorded as state first and performed after the
commit — so a replay, a retry, or two merged branches can reconstruct the
decision without repeating the action.**

No LLM anywhere: deterministic by design (§67), so the claim is a number in
the output, not a promise in prose. `PendingAction.status` (built into the
outbox primitive, `reactifact/interrupt.py`) plus the fake `SentLog` are the
proof.

## Run

```bash
.venv/bin/python -m examples.outbox.main
```

```
1. commit -> dispatch
   produce wrote a PendingAction; the dispatcher sent after the commit.
   sent=['notify:42'] action.status=dispatched
   [confirmed] the produce did no I/O itself; exactly one send.

2. re-derivation (a later edit re-runs the produce)
   first run sent 1; the re-run sent 0.
   [confirmed] stable key -> the re-run created no new intent.

3. same generation (two producers, one event)
   producers=2 intents=1 sent=1
   [confirmed] the outbox deduped at drain; one delivery.

4. two merged branches (the same intent on both forks)
   branches=2 intents-after-merge=1 sent=1 pending-after-flush=0
   [confirmed] merge ignored dispatch bookkeeping; external key gave one send.

5. replay (reconstruct state, do not act again)
   sent before replay=1 after replay=1
   replayed state: dispatched=1 pending=0
   [confirmed] replay rebuilt the answer without re-sending anything.

6. failure -> retry (a failed dispatch is state, not a lost effect)
   first dispatch raised 'smtp temporarily unavailable'; status was 'failed'
   after retry_action + flush: status=dispatched sent=['notify:55']
   [confirmed] the failure stayed visible and retry delivered exactly once.

7. app-owned retry (a wrapper around the dispatcher)
   a transient outage absorbed by the wrapper; status=dispatched attempts=0 sent=['notify:77']
   [confirmed] backoff/retry lived in the app; the runtime saw one clean outcome.
```

## The split in one produce

```python
@produce(Receipt, also_creates=[PendingAction])
async def process_order(call: ProduceCall) -> None:
    order = call.trigger
    # the side effect is recorded, not performed
    call.effects.act(
        "notify",
        key=f"notify:{order.data.id}",          # stable, content-derived
        payload={"to": order.data.customer, "order": order.data.id},
    )
    call.effects.upsert(Receipt(...), id=f"receipt:{order.data.id}")
```

and the runtime performs it after the commit, exactly once per id:

```python
await Runtime(context, agents=AGENTS, dispatcher=dispatch).arun()
```

The produce never touches the network. That is the whole trick: `key` is
derived from the order, not from a counter or a timestamp, so re-deriving it
can't produce a second intent — and the runtime drains whatever was *actually
committed*, not whatever a produce tried to send.

## Why the obvious alternatives fail

- **Send inside the produce.** A retry (resume is at-least-once, §42) or a
  crashed generation re-runs the produce and sends again; replay can't
  reconstruct without re-sending.
- **Guard with `effects.create_once` only.** The guard is resolved at commit,
  but two produces in one generation run against the same pre-commit snapshot
  — both see "not created yet", both would send. The outbox dedupes *at drain*,
  after commit, so it sees one intent (case 3).
- **Rely on the merge to dedupe.** Merge is three-way over *state*, and the
  dispatch bookkeeping (`status`/`dispatched_at`) is not part of the intent, so
  the same action on two forks converges instead of raising `MergeConflict`
  (case 4) — but merge itself never dispatches: `flush_pending_actions()` is
  how a merged, undispatched action gets sent.

## Honest caveat

Delivery is still **at-least-once**, not exactly-once: a crash between the real
send and the `dispatched` commit re-runs the dispatch. That is why the demo
passes `idempotency_key` to the fake external system, which keeps its own
`SentLog` guard — the same contract a real email/webhook API must honor. The
framework cannot make the *I/O* exactly-once; it makes the *intent* replay-safe.

Two more things stay with the application (the runtime deliberately does not own
them): a **periodic drain** (`flush_pending_actions()` on startup and on a timer,
so an action committed just before a crash is not stranded) and the **retry
policy** — case 7 wraps the dispatcher with `with_retries(...)`; `on_dispatch_error`
and `retry_action(id)` are the runtime-level hooks. See
[durability → Outbox in production](../../docs/en/durability.md#outbox-in-production).

## See also

`examples/forklab` is the branch/merge half of this story: two strategies on
their own forks, reconciled with a three-way `merge()`. This example shows the
outbound-effect half — the same merge, but the thing that merges is an action
that must fire exactly once. `docs/en/patterns.md#outbox-external-side-effects`
has the recipe; `docs/en/durability.md` states the at-least-once contract.

## Structure

```
outbox/
├── models.py       # Order (input) + Receipt (committed state)
├── produce.py      # records the intent via effects.act — never does the I/O
├── agents.py       # order_agent · mirror_agent (same-generation duplicate)
├── dispatcher.py   # the fake external world: SentLog + idempotent dispatcher
└── main.py         # seven cases: commit→dispatch, retry, same-gen, merge, replay, failure, app-owned retry
```
