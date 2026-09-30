# Effects reference

`call.effects` is the produce-scoped change set. Nothing is applied until the
runtime commits the turn's patch, so effects are atomic, concurrency-safe and
need no rollback. Do not build `Patch` objects by hand unless you are writing a
recipe — the effect API is the language.

## Creating

```python
# not-run: illustrative
handle = call.effects.create(Answer(text="..."))          # random id
handle = call.effects.create(Answer(text="..."), id="ans:q1")  # stable id
call.effects.create_once(SearchDone(qid=q), id=f"search:{q}")   # never twice
call.effects.upsert(Summary(qid=q, text="..."), id=f"summary:{q}")  # create or replace
```

- **Stable ids** make a re-waking produce idempotent: a second run with the
  same id is a no-op for `create_once`, a replace for `upsert`.
- `create` returns a `Handle` — a valid link target *before* the artifact is
  committed, so you can declare provenance immediately.

## Updating, deleting, linking

```python
# not-run: illustrative
call.effects.update(report, status="approved")   # patch fields of an existing artifact
call.effects.link(answer, "supported_by", evidence)
call.effects.unlink(answer, "supported_by", evidence)
call.effects.delete(scratch)                     # remove an artifact
```

`link`/`unlink` also take a `Handle` or an id string in place of an
`Artifact`. A link is a first-class provenance edge (see
`references/effects-and-state.md` in the `reactifact` skill).

## Asking a human (HITL)

```python
# not-run: illustrative
call.effects.ask("Approve this report?", kind="approve")
# later, when the answer arrives:
call.effects.resume(question, resolution="approved")
```

`ask` emits a `PendingQuestion` and pauses the flow; an agent consumes the
resolved question and calls `resume`. `reactifact.recipes.ApprovalGate` wraps
the common "sign off before finalizing" shape.

## Outbound side effects (outbox)

Never do external I/O — send an email, call a webhook — inside a produce. Record
the intent; a `Runtime(dispatcher=...)` performs it once after the commit:

```python
# not-run: illustrative
call.effects.act("notify", key=f"notify:{order_id}:email", payload={"to": email})
```

`act` writes a `PendingAction` under the stable id `action:{key}` (so retries and
merges never create a second one), and returns `None` if it already exists. The
dispatcher gets `(context, action)`; mark it done by having the runtime record
`dispatched` (or have the dispatcher raise — the action is marked `failed`).
Replay reads the record back without re-sending it. See
`reactifact-observability` for the replay side.

## Reading current effects

`call.effects` is available inside a produce. Outside it, the module-level
`current_effects()` returns the active `Effects` (or `None`) — the runtime
pushes a fresh slot per produce via a contextvar, so parallel produces never
see each other's effects.

`is_empty()` tells you whether the produce is about to change anything;
`to_patch()` compiles the effects into the `Patch` the runtime merges.

## Why not mutate?

Assigning to `context.state[...]` or to `artifact.data` would change state
outside the patch machinery: no atomicity, no events, no trace, no optimistic
concurrency. Effects exist so every change is one commit — a run either
produces its whole intended change or none of it.
