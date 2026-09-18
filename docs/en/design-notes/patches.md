# Design note — how we build patches in the examples

**Status:** discussion draft · **Scope:** the "patch logic feels heavy" feedback
on the bundled examples · **Options analyzed:** A (ergonomics), B (imperative
produces), C (builder redesign).

---

## 1. Problem statement

Reading the examples, the *shape* of an effect is fine — `Patch().create(...)`,
`.update_fields(...)`, `.link(...)` are a small, explicit vocabulary. What reads
as complexity is:

1. **Composing several effects into one returned patch.** All state changes of
   a produce must land in a single `Patch`. In `repair` this composes as
   `Patch.merge_existing_patch(update_project, reply)` — 10 call sites, all in
   `stages.py`.
2. **The message-reply glue.** Most stages do "update the project → mark the
   handled message → create a ChatReply" on every turn. `_update_project` +
   `_reply` + a compose call is repeated boilerplate.
3. **Two mental models coexist.** `Produce` classes *return a patch*; some
   `Agent.run` bodies (and early demos) *mutate `context` directly*. Neither is
   wrong — but "which one should I write here?" is not answered by the docs.
4. Big guard/if-else produce bodies return different composed patches, which
   compounds 1.

This is not evidence that patches are wrong. It is evidence that the *authoring
ergonomics* and the *example layout* carry repetition that the core does not
need to — and that the two models should be reconciled or made explicit.

## 2. Evaluation criteria

| Criterion | What we actually care about |
|---|---|
| **Atomicity** | a produce either applies all its effects or none — no half-applied state (§41) |
| **Validation** | declared `produces` types are enforced (no mystery artifacts) |
| **Readability** | a produce body reads top-down, the intent is visible in one screen |
| **Idempotency** | stable ids + guards keep re-runs from duplicating (§42) |
| **Failure model** | honest fallbacks: a failed generate returns `None`, never a crash |
| **Audit / provenance** | reads/writes/relations are recorded per commit (§34, §54) |
| **Server cost** | runtime changes stay small; examples stay the primary docs |

## 3. Today: the rule that creates the boiler

> A `Produce.produce` must **return** a complete `Patch`; the runtime applies it
> as one commit.

Consequences:

- Because effects must be *expressed before* they are applied, every produce
  becomes a builder: create refs here, update that, reply there — then compose.
- The compose API is `Patch.merge_existing_patch(a, b, c)` — verbose, and a
  session-hint of "one patch per produce" without syntactic sugar.
- Atomicity and validation are **free**: nothing touches the context until the
  whole patch is assembled; `_validate_patch_types` runs on the finished patch.

## 4. Option A — ergonomics, keep the model

**4.1 `Patch.__or__` (merge operator).**

```python
return reply | update | question          # instead of merge_existing_patch(r, u, q)
```

Semantics: `a | b` returns a new `Patch` whose operations are `a.ops + b.ops`
(non-mutating; `a |= b` optional). `merge_existing_patch` stays as an alias for
the old call sites and for code that likes the explicit name.

- Atomicity / validation: unchanged (still one assembled patch).
- Readability: ~10 nested compose calls collapse to flat chains.
- Cost: ~20 lines in `patches.py` + touching the 10 call sites.
- Idempotency / failure / audit: untouched.

**4.2 Example-side responder kit** (domain: `ChatReply` etc. stay in the demo).

```python
# repair/produce/common.py
def respond(project_art, msg_id, text, *, updates=None, kind="text", images=None) -> Patch:
    changes = {"handled_msg": msg_id} | (updates or {})
    return _reply(context_dep, msg_id, text, kind, images) | _update_project(project_art, changes)
```

A stage then reads:

```python
return respond(self._ctx, msg.id, "Выберите вариант:\n" + previews,
               updates={"stage": "design_choice", "design_options": options, ...},
               images=[...])
```

The `context` dependency is either passed in or the kit becomes a tiny stage
mixin. The same kit shape applies to `knowledge`/`medic-lab` where replies exist.

- Trade-off: it is per-example boilerplate *by design* (the reply artifact is a
  domain model, not a framework type). Genericity would push `ChatReply` into
  core — against §4/§28 (artifacts are domain). We deliberately keep it in the
  demo and duplicate a small helper; the duplication is ~15 lines per demo.

**Result of A:** no semantic change; `stages.py` loses its nested compose and
most of the glue; tests are a free safety net.

## 5. Option B — imperative produces (reconcile the two models)

Give a produce the *right* to mutate the context directly; the runtime wraps
whatever it changed into one commit.

```python
async def produce(self, call: ProduceCall) -> None:
    if <guard>: return None
    call.context.create(ChatReply(...), id=f"reply:{msg_id}")
    call.context.update("project:1", new_project)   # or context.patch(...)
    return None                                     # effects already applied
```

**The hard part is atomicity.** Today, atomicity is structural: the patch is
assembled in memory and applied exactly once. With imperative mutation, changes
land in the working tree *during* the produce, and an unhandled exception would
leave them behind. To preserve §41 the runtime would have to:

- snapshot the affected artifact set before running the produce,
- on success, diff the post-state into operations and commit them as one commit
  (recomputing writes + relations for the trace, and re-validating against
  `produces`),
- on failure, roll back the snapshot.

Costs and risks:

- `context.create/update/delete` already emit events; auto-commit must avoid
  double-firing (the events are intended for *other* agents, and the committing
  produce must not re-trigger itself by accident — needs suppression or
  drain-before-commit).
- Validation moves from "before apply" to "after diff" — possible, but the
  error now happens later in the run.
- Trace coverage (reads/writes/relations) must be derived from the diff rather
  than from `patch.operations` — a second provenance path to maintain.
- The "return a patch" route stays (some produces genuinely assemble a set of
  independent artifacts, e.g. `fan_out_sources`), so **both** models live in the
  runtime forever: more surface, more docs.

These are not insurmountable, but they are a *runtime feature* with real
complexity (transactionality on the context), bought for ergonomics that
Option A mostly delivers already. Worth a spike on a branch, not a fast commit.

## 6. Option C — redesign the builder

`Patch.init(...)...end()`, `link_many`, receipt/list helpers, etc.

- It competes with A for the same surface without removing the *reason* for the
  boiler (return-one-patch + compose). A is a sub-recipe of C's "sugar" bucket.
- Higher conceptual cost, low additional payoff. **Rejected** unless A proves
  insufficient in practice.

## 7. Comparison

| | A (sugar + kit) | B (imperative) | C (builder) |
|---|---|---|---|
| Atomicity | unchanged (structural) | needs rollback logic in runtime | unchanged |
| Validation | unchanged | after-diff, later in run | unchanged |
| Readability | high, flat chains | highest (linear bodies) | medium |
| Idempotency | unchanged | unchanged (guards still rule) | unchanged |
| Failure model | unchanged (`None` paths) | unchanged | unchanged |
| Audit | unchanged | second provenance path needed | unchanged |
| Runtime change | none | significant | none |
| Risk | low | medium-high | low |

## 8. Recommendation

1. **Do A now:** `Patch.__or__` (+ keep `merge_existing_patch` as alias), and a
   small `respond(...)`/stage kit in `repair` (mirroring into the other demos
   where the reply glue recurs). Tests already cover the behavior.
2. **Document the rule:** "a produce returns one Patch; compose effects with
   `|`; the message-reply cycle belongs to a stage kit, not the core." Add it to
   `patterns.md`.
3. **Spike B separately** (branch, not mainline): a preview flag
   `Produce.imperative = True` where the runtime snapshots→diffs→commits and
   rolls back on exceptions. Decision to keep B is deferred until the spike
   shows the rollback path stays simple with events/traces.

## 10. Decision (updated)

**`Effects` was adopted as the authoring surface** (§24). A produce writes
`self.effects.create/update/link/ask(...)` and returns `None`; the runtime
pushes a fresh effect slot per execution (contextvar — concurrency-safe) and
compiles it into one atomic patch. `Patch` is now the runtime's *transport*
type, not something users assemble:

- Demos, `recipes` (`fan_out_sources`, `materialize_doc`), `StatusMachine`,
  the tool loop (`ToolUse`/`ToolUseHITL`) and HITL (`effects.ask`) all use
  effects; `Patch`-returns remain only as an internal/advanced escape hatch.
- The `merge_existing_patch` / responder-glue boiler in the examples is gone —
  produces are linear: guard → compute → `self.effects.*` → `None`.
- Atomicity stays structural (nothing is applied until the runtime compiles the
  slot), so no rollback machinery was needed — the "imperative B" concern does
  not apply to declarative effects. Runtime op ordering: a produce's *returned*
  patch (if any) goes first, then its effects, preserving create→link and
  update ordering (§12 end-to-end).

Option B's imperative-mutation variant and Option C remain rejected.

## 11. Open questions

- Does `|` need an in-place `|=` for the common `patch = patch | reply` case?
- Should the responder kit move into `recipes` as a *documented example-only*
  pattern — or stay per-demo by convention?
- For B: can `context.create/update` distinguish "internal work-in-progress"
  from "cross-agent trigger" without suppressing events globally?