# Durability & resume

reactifact is single-process and has no broker, but a **session-backed run
survives a process restart**: reopen the session and keep going instead of
losing the work an interrupted run had queued. This page states exactly what
that guarantee is — and what it is not.

## What is persisted

A `SessionStore` saves the whole `Context` (`Context.to_dict`) under a
`session_id`:

- the **artifacts** (typed, versioned) and the **relations** between them;
- the **commit chain** (`Context.history()`), so provenance and `diff`/
  `rollback`/`replay` still work;
- the **pending trigger queue** — the events no generation has dispatched yet.

With the default `session_save_policy="per_commit"`, the runtime saves at every
**generation boundary**, *after* that generation's trigger batch is consumed.
So the saved snapshot is consistent: an already-consumed trigger is not replayed,
and one whose consumer never ran is.

```python
from reactifact import Runtime, SessionStore
from reactifact.checkpoints import SQLiteKVBackend

store = SessionStore(SQLiteKVBackend("sessions.sqlite3"))
session = await store.open("user-1")            # loads, or creates if absent
runtime = Runtime(session.context, agents=[...], session=session)
await runtime.arun()
```

```text
# after a crash / restart — a brand-new process, nothing in memory
session = await store.open("user-1")
runtime = Runtime(session.context, agents=[...], session=session)
await runtime.arun()   # continues: pending triggers are still there
```

A crashed run therefore resumes from the **last committed generation**, and any
trigger that was queued but not yet reacted to is re-dispatched.

## The guarantee: at-least-once

Resume is **at-least-once**, not exactly-once. A produce that had already
committed before the crash may run again. If it is not idempotent, that means a
duplicate artifact (or a repeated external side effect).

Give produces a **stable id** so a re-run upserts instead of duplicating
([§42](../constitution.md)):

```python
@produce(Summary)
async def summarize(call):
    doc = call.trigger
    # A stable id makes the re-run idempotent: creating it twice returns the
    # same artifact and emits no second event.
    call.effects.create(Summary(text=...), id=f"summary:{doc.data.url}")
```

`effects.create_once_from(...)` (stable id derived from the trigger) and
`Consume(..., debounce=True)` are the other tools for the same goal.

## What is *not* covered

- **External side effects.** An email, a file write, a third-party API call
  inside a produce can be repeated by a retry. The framework cannot make those
  exactly-once — guard them with your own idempotency key. (`call.request` is
  available for a correlation id; see `reactifact.request`.)
- **In-flight work.** A produce that was mid-flight at the crash is simply
  re-run; only committed generations are durable.
- **Budgets reset per run.** `Budget` counters (`max_runs`, `max_seconds`) are
  per-`arun()` call; resuming starts a fresh budget, not the remainder of the
  old one.
- **`per_turn` granularity.** With `session_save_policy="per_turn"` the session
  is saved once at the end of a turn, so a crash mid-turn rolls the whole turn
  back and re-runs it. `per_commit` (default) saves at every generation boundary
  instead.

## Resume vs. replay

Two different operations, often confused:

- **Resume** (`session.open(...)` + `Runtime.arun()`) continues an *interrupted*
  run: it re-dispatches pending triggers and may re-run produces.
- **Replay** (`reactifact replay`, `ReplayLLM`) reconstructs *why a completed
  run reached its state*, deterministically, without re-running agents — see
  [Replay](replay.md).

The first is execution; the second is audit.

## Related

- [Execution model](scheduler-semantics.md) — the generation loop and its
  explicit non-guarantees.
- [Concepts](concepts.md) — `Context`, commits, and the event queue.
- [Replay](replay.md) — verifying a finished run's `context_hash`.
