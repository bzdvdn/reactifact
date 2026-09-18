# The produce contract & mental model (§24)

One paragraph to keep in your head while reading any example or writing a
produce:

> **A produce describes what should change by writing `self.effects`; the
> runtime compiles those effects into one atomic patch and commits it. You
> almost never build a `Patch` yourself — it is the runtime's transport.**

```python
async def produce(self, call: ProduceCall) -> None:
    if <guard>:                     # eligibility is a decision of the state
        return None
    # effects: the produce's "diff, expressed"
    evidence = self.effects.create(Evidence(...), id="evidence:q1")
    answer = self.effects.create(Answer(...), id="answer:q1")
    evidence.link("extracted_from", doc)     # doc: Artifact
    answer.link("supported_by", evidence)    # evidence: an effect handle
    self.effects.update(turn, status="answered")
    self.effects.ask("Approve the estimate?", kind="approval")   # HITL (§60)
    return None
```

`call` (a `ProduceCall`) is the one argument every produce receives —
`.context`/`.inputs`/`.event`/`.trigger`/`.effects` (the last one identical to
`self.effects` above; useful for the function form below, which has no
`self`).

## Idempotency sugar: `create_once` and `upsert`

A re-derived id (`f"answer:{qid}"`) needs a guard — every produce above the
`self.effects.create(...)` line usually has one. `create_once` folds it into
the call itself:

```python
handle = self.effects.create_once(Answer(...), id=f"answer:{qid}")
if handle is None:
    return None  # already answered — nothing to do
```

`Create` on an existing id is already create-or-refresh (a new version of the
same logical entity, §42/§43) — `upsert` is just the explicit name for that,
for the call sites where "this may already exist" is the point, not a
surprise:

```python
self.effects.upsert(Summary(...), id=f"summary:{doc_id}")
```

## The three-layer picture

| Layer | What it is | Who writes it |
| --- | --- | --- |
| **Produce** | the reaction: guard → LLM/calc → `self.effects.*` → `None` | the application (you) |
| **Effects** | the stated change-set (creates/updates/links/questions), scoped to the turn | you, via `self.effects` |
| **Patch** | the *compiled* operations the runtime applies as one commit | the runtime (and legacy/advanced assemblies) |

Nothing is applied until the produce returns — **atomicity is structural** (§41),
no rollback machinery. Events, validation against `produces`, and trace
reads/writes/relations are all derived from the same compiled ops.

## Why `self.effects` is ambient

`self.effects` lives in a produce-scoped slot that the runtime pushes before
each execution and pops after (a contextvar) — safe under parallel produces, and
**invisible** to you: you never construct it, never name it, never pass it. You
can build handles across statements (`evidence` created above is linked below),
which is why the call sites read "about artifacts", not "about ids".

## Where `Patch` still shows up

- **`Agent.run`** — the escape hatch for custom (non-Produce) agents that
  assemble a change-set by hand; the runtime merges it after the effects.
- **Recipes** (`fan_out_sources`, `materialize_doc`) and `StatusMachine` write
  into the slot; the tool loop (`ToolUse`/`ToolUseHITL`) and HITL
  (`effects.ask`) are effects too.
- **Tests and advanced assembly** may still build a `Patch`; in ordinary
  produces you should not need it.

## The rule of thumb

```text
guard → decide → describe (self.effects) → return None
```

If you find yourself writing `Patch()` inside a produce — stop and use
`self.effects`; the runtime does the compiling.

## The function form (`@produce`) — same authoring surface

A decorator produce receives the same `call.effects` — one argument, `call`,
exactly like the class form above:

```python
from reactifact import produce

@produce(Answer)
async def answer_turn(call):
    if not call.inputs:
        return None
    qid = call.inputs[0].id
    ans = call.effects.create(Answer(text=...), id=f"answer:{qid}")
    call.effects.link(ans, "derived_from", call.inputs[0])
    call.effects.update(turn, status="answered")
    return None
```

The return-based contract still works: returning a model / list of models /
`Patch` / `None` is compiled by the runtime, so short produces stay
one-liners — `def f(call): return Answer(...)` reads `call.context`/
`call.inputs`/`call.trigger` for whatever it needs and returns the result
instead of writing `call.effects.create(...)` itself.

## Which style to reach for

The subclass and `@produce` function above are the two canonical styles —
pick subclass when the produce has its own logic worth naming as a class,
`@produce` for a short one-off. One other thing `Produce`/`Agent` accepts is
*not* on that list on purpose:

- Overriding `Agent.run(self, event, context) -> Patch` directly, bypassing
  `effects`/`Produce` entirely to hand-assemble a `Patch`. A low-level,
  internal escape hatch for cases effects genuinely can't express — no
  example in this repo uses it, only this repo's own tests.