# Effects reference

`Effects` is the step-scoped change set. Nothing is applied until the runtime
commits the patch, so effects are atomic, concurrency-safe and need no rollback.
Do not build `Patch` objects by hand unless you are writing a recipe — the
effect API is the language.

Take `effects: Effects` as a typed parameter in `run()`/`@step`, or use the
return-style instructions below.

## Prefer return-style first

For a short step, return the top-level instruction instead of reaching for
`effects`:

```python
# not-run: illustrative
return Ensure(Answer(text="..."), id=f"answer:{qid}")  # idempotent create
return Emit(Answer(text="..."), id=f"answer:{qid}")    # create or refresh
return Edit(ticket, status="closed")                   # patch fields
return Drop(scratch)                                   # delete
return Relate(claim, "derived_from", evidence)         # provenance edge
```

`Ensure`/`Emit`/`Edit`/`Drop`/`Relate` compile into the same atomic patch; they
keep the step signature free of `effects: Effects`.

## Creating with effects

Use the effect API when you need a handle to link before commit, or when the
step already holds `effects` for batch/conditional writes:

```python
# not-run: illustrative
handle = effects.create(Answer(text="..."))              # random id
handle = effects.create(Answer(text="..."), id="ans:q1") # stable id
effects.create_once(SearchDone(qid=q), id=f"search:{q}") # Ensure equivalent
effects.upsert(Summary(qid=q, text="..."), id=f"summary:{q}")  # create/replace
```

- **Stable ids** make a re-waking step idempotent: a second run with the same id
  is a no-op for `create_once`, a replace for `upsert`.
- `create` returns a `Handle` — a valid link target *before* the artifact is
  committed, so you can declare provenance immediately.

## Updating, deleting, linking

```python
# not-run: illustrative
effects.update(report, status="approved")   # patch fields of an existing artifact
effects.link(answer, "supported_by", evidence)
effects.unlink(answer, "supported_by", evidence)
effects.delete(scratch)                     # remove an artifact
```

`link`/`unlink` also take a `Handle` or an id string in place of an `Artifact`.
A link is a first-class provenance edge (see
`reactifact/references/effects-and-state.md`).

## Asking a human (HITL)

```python
# not-run: illustrative
from reactifact import Human

async def run(trigger: Artifact[Any], human: Human) -> None:
    human.ask("Approve this report?", kind="approve")
```

`human.ask` emits a `PendingQuestion` and pauses the flow; an agent consumes the
resolved question and calls `human.resume`. `reactifact.recipes.ApprovalGate`
wraps the common "sign off before finalizing" shape.

## Outbound side effects (outbox)

Never do external I/O — send an email, call a webhook — inside a step. Record
the intent; a `Runtime(dispatcher=...)` performs it once after the commit:

```python
# not-run: illustrative
from reactifact import Outbox

async def run(trigger: Artifact[Any], outbox: Outbox) -> None:
    outbox.act(
        "notify",
        key=f"notify:{order_id}:email",
        payload={"to": email},
    )
```

`act` writes a `PendingAction` under the stable id `action:{key}` (so retries and
merges never create a second one), and returns `None` if it already exists. The
dispatcher gets `(context, action)`; mark it done by having the runtime record
`dispatched` (or have the dispatcher raise — the action is marked `failed`).
Replay reads the record back without re-sending it. See
`reactifact-observability` for the replay side.

## Reading current effects

The injected `effects` is available inside a step. Outside it, the module-level
`current_effects()` returns the active `Effects` (or `None`) — the runtime
pushes a fresh slot per step via a contextvar, so parallel steps never see each
other's effects.

`is_empty()` tells you whether the step is about to change anything;
`to_patch()` compiles the effects into the `Patch` the runtime merges.

## Why not mutate?

Assigning to `context.state[...]` or to `artifact.data` would change state
outside the patch machinery: no atomicity, no events, no trace, no optimistic
concurrency. Effects exist so every change is one commit — a run either
produces its whole intended change or none of it.
