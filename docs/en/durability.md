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

- **External side effects inside a produce.** An email, a file write, or a
  third-party API call made *directly* in a produce can be repeated by a retry.
  Do not do that — record an intent instead: `self.effects.act(kind=..., key=...,
  payload=...)` writes a `PendingAction` and `Runtime(dispatcher=...)` performs
  it once after the intent commits (see the outbox pattern in
  [Patterns](patterns.md#outbox-external-side-effects)). The framework still
  cannot make the I/O *itself* exactly-once — the dispatcher must be idempotent
  on `idempotency_key` (passed through in `action.data`), which closes the
  residual at-least-once window. (`call.request` is available for a correlation
  id; see `reactifact.request`.)
- **No background relay.** The framework never runs a process that keeps
  retrying the outbox on its own: the next `arun()` drains committed actions,
  and `flush_pending_actions()` drains them without a generation — call it on
  startup and periodically (in your app's own timer) so an action committed
  just before a crash is not stranded. Retry policy is the application's: a
  failing dispatcher marks the action `failed` (bumping `attempts`), fires
  `Runtime(on_dispatch_error=...)` if set, and re-raises; `context.retry_action(
  id)` re-arms it.
- **In-flight work.** A produce that was mid-flight at the crash is simply
  re-run; only committed generations are durable.
- **Budgets reset per run.** `Budget` counters (`max_runs`, `max_seconds`) are
  per-`arun()` call; resuming starts a fresh budget, not the remainder of the
  old one.
- **`per_turn` granularity.** With `session_save_policy="per_turn"` the session
  is saved once at the end of a turn, so a crash mid-turn rolls the whole turn
  back and re-runs it. `per_commit` (default) saves at every generation boundary
  instead.

## Outbox in production

The outbox primitive is safe by construction, but it is a **library feature,
not a delivery service**: it runs no relay and invents no retry policy. To ship
outbound effects (email, webhooks, payments) in production you own four things:

1. **An idempotent dispatcher.** Pass `action.data.idempotency_key` to the
   external system so the at-least-once window (a crash between the send and the
   `dispatched` commit) is closed on the far side. Never mint a new key on a
   retry.
2. **A drain.** Call `flush_pending_actions()` on startup and periodically from
   your own timer, so an action committed just before a crash is not stranded.
   The next `arun()` drains too — but only if a turn actually runs.
3. **A retry policy.** A failing dispatcher marks the action `failed` (bumping
   `attempts`) and re-raises; wrap your dispatcher with your own retry/backoff,
   and/or hook `Runtime(on_dispatch_error=...)` to alert and
   `context.retry_action(id)` to re-arm. Backoff is deliberately not in the
   runtime.
4. **Observability.** Query `context.list_artifacts(PendingAction)` (or the
   `replay` CLI) for `failed` and long-pending actions and alert on them.

With those four in place it is production-ready; if you want the framework
itself to keep retrying with no app timer, that is a broker/relay — out of scope
by design (§75). `examples/outbox` shows the dispatcher-level retry wrapper.

## Schema evolution

A persisted artifact's type is stored as a string (`Artifact.data_type`, the
`data_type` of a compiled `Create`/`Update`, `Event.artifact_type`) — by default
the Python qualified name (`module.Qualname`). **You do not have to register
anything**: on save the id is derived automatically, and on load `resolve`
imports the qualified name back, so a normal module-level model round-trips with
no boilerplate. `reactifact.types` exists only to decouple that stored identity
from Python's module layout when you want to.

Registration matters in three cases:

- **Renames or moves** — a stable `type_id` keeps old sessions/recordings
  loadable after a model is renamed or moved between modules.
- **Aliases** — payloads carrying a *previous* id keep resolving.
- **Schema migration** — a `migrate` hook renames fields / fills defaults before
  validation.

```python
from reactifact import register_type

register_type(
    Answer,
    type_id="answer",                                   # written from now on
    aliases=["old.pkg.Answer"],                         # what old payloads carry
    migrate=lambda d: {**d, "prose": d.pop("text", "")},  # a field rename
)
```

When you only need a stable id (no aliases/migration), a `TYPE_ID` classvar on
the model is enough — no call needed. Call `register_type` at import time, next
to the model.

- `type_id_of(model)` — the id written on save: a registered `type_id`, a
  `TYPE_ID` classvar, else the qualified name.
- `resolve(type_id)` — the class on load: registry → alias → import by qualified
  name, so pre-registry payloads keep loading.
- `migrate` runs on the raw dict before validation (field renames, defaults).

**The one real caveat**: a qualified name is only loadable if the class is
importable in the *new* process. A model defined in `__main__`, in a notebook
cell, inside a function (`<locals>`), or built with `pydantic.create_model` is
not — its id will not resolve on resume/replay. Give such a model an explicit
`type_id` (and a real module to live in); `resolve`'s error names the id it
could not find and points at `register`.

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

## Deadlines, graceful shutdown & in-flight runs

`Budget.max_seconds` is enforced as a **hard** per-turn deadline: the runtime
cancels the generation in flight (a hung LLM call is actually stopped) rather
than only checking between generations. The cancelled generation's trigger batch
is not consumed, so a later `arun()` resumes it; the turn ends with
`RunOutcome.BUDGET_TIME_EXCEEDED`.

On process shutdown, `Runtime.request_stop()` asks the run to stop at the next
generation boundary — the current generation finishes (its commit lands), no new
one starts, and `RunOutcome.STOPPED` is set. `await runtime.ashutdown(timeout=…)`
does that and waits for the turn, force-cancelling after `timeout`. It never
closes shared `RuntimeResources`; close those in your own lifespan
(`resources.aclose()` or a `ResourceScope`).

The process's in-flight turns are visible read-only:

```python
from reactifact.runtime import active_runs, cancel_run

len(active_runs())           # a readiness signal
active_runs()[0].session_id  # what is running
cancel_run(run_id)           # cancel one turn
```

`active_runs()` is in-process only — single event loop, not persisted, not
cross-process. It is an ops/readiness view, not a task queue.

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
