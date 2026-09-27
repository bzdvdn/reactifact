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

## Schema evolution

A persisted artifact's type is stored as a string (`Artifact.data_type`, the
`data_type` of a compiled `Create`/`Update`, `Event.artifact_type`) — by default
the Python qualified name, so renaming or moving a model would break loading old
sessions and recordings. `reactifact.types` decouples the persisted identity
from Python's module layout:

```python
from reactifact import register_type

register_type(
    Answer,
    type_id="answer",                                   # written from now on
    aliases=["old.pkg.Answer"],                         # what old payloads carry
    migrate=lambda d: {**d, "prose": d.pop("text", "")},  # a field rename
)
```

- `type_id_of(model)` — the id written on save: an explicit `type_id`, a
  `TYPE_ID` classvar, else the qualified name (nothing changes until you opt in).
- `resolve(type_id)` — the class on load: registry → alias → import by qualified
  name, so pre-registry payloads keep loading.
- `migrate` runs on the raw dict before validation (field renames, defaults).

This covers artifacts, commit operations and events — a whole session's state
*and* its pending trigger queue — so an upgrade that renames/moves a model no
longer breaks resume, replay or golden snapshots.

## Resume vs. replay

Two different operations, often confused:

- **Resume** (`session.open(...)` + `Runtime.arun()`) continues an *interrupted*
  run: it re-dispatches pending triggers and may re-run produces.
- **Replay** (`reactifact replay`, `ReplayLLM`) reconstructs *why a completed
  run reached its state*, deterministically, without re-running agents — see
  [Replay](replay.md).

The first is execution; the second is audit.

## Bounding a long-lived context

A session that lives for many turns keeps every commit and every artifact
version forever — memory (and `to_dict`) grows without bound. `Context.compact`
bounds it the way `git` squashes history:

```python
session.context.compact(keep_commits=50, keep_versions=2)
```

It collapses every commit older than the last `keep_commits` into a baseline
snapshot and (with `keep_versions`) trims each artifact's retained version
history. The absolute `version`/`head_id` are preserved, so `context_hash`,
commit version numbers and resumes are unaffected.

Two honest consequences:

- **It is irreversible.** `checkout`/`diff` below the resulting baseline raise
  (`Context.compacted_at` reports it), and provenance for artifacts last written
  before the baseline is gone — the operations that produced them were collapsed.
- **It does not evict artifacts.** To drop old artifacts (e.g. out-of-window
  messages), use the memory recipes (`WindowPruner`, `RollingDigestSummarizer`,
  [Recipes](recipes.md)); `compact` bounds the *history*, not the working set.

## Related

- [Execution model](scheduler-semantics.md) — the generation loop and its
  explicit non-guarantees.
- [Concepts](concepts.md) — `Context`, commits, and the event queue.
- [Replay](replay.md) — verifying a finished run's `context_hash`.
