# The step contract & effects (§24)

One paragraph to keep in your head while reading any example or writing a
step:

> **A step describes what should change by writing effects, or returns its
> output; the runtime compiles the change-set into one atomic patch and commits
> it. You almost never build a `Patch` yourself — it is the runtime's
> transport.**

```python
from reactifact import Artifact, Effects

async def run(
    self,
    doc: Artifact[Document],
    turn: Artifact[Turn],
    effects: Effects,
) -> None:
    evidence = effects.create(Evidence(...), id="evidence:q1")
    answer = effects.create(Answer(...), id="answer:q1")
    evidence.link("extracted_from", doc)
    answer.link("supported_by", evidence)
    effects.update(turn, status="answered")
    effects.ask("Approve the estimate?", kind="approval")   # HITL (§60)
```

A class step that writes effects declares an `effects: Effects` parameter in
`run()` — the runtime injects the step-scoped slot. A `@step` function receives
the single `call: StepCall` argument and writes `call.effects`; both reach the
same effect set, just through a different surface (the function form has no
`self`).

Inputs can be selected inline: `Annotated[T, When(...)]` conditions a type, and
`Annotated[T, Wakes(False)]` reads it as input without waking the step:
`history: Annotated[ConversationHistory, Wakes(False)]`. For `debounce`,
custom event types, or subclass matching, use `Consume(...)` explicitly.

`scope` correlates typed inputs. `scope = "query_id"` is the shorthand; when a
correlated artifact is identified by its *id* rather than a data field, name the
owner:

```python
scope = Scope("question_id", owner=Question)
```

Now `Question` matches by `artifact.id`, while `Hypothesis`/`Evidence`/`Claim`
match by `data.question_id`. `Scope(..., key=fn)` supplies a custom extractor
when the key lives in `notes` (e.g. a `PendingQuestion`).

## Return vs effects

Returning a model (or a list of models) is create-sugar:

```python
async def run(self, question: Artifact[Question]) -> Answer:
    return Answer(text=question.data.text)
```

Use `effects` when the step needs an explicit id, update/link/delete, HITL, or
more than one create. Returning `None` means "no changes" (a guard or a branch
that did not apply).

## Idempotency sugar: `create_once` and `upsert`

A re-derived id (`f"answer:{qid}"`) needs a guard — `create_once` folds it into
the call itself:

```python
handle = effects.create_once(Answer(...), id=f"answer:{qid}")
if handle is None:
    return None  # already answered — nothing to do
```

`Create` on an existing id is create-or-refresh (a new version of the same
logical entity, §42/§43); `upsert` is the explicit name for that:

```python
effects.upsert(Summary(...), id=f"summary:{doc_id}")
```

## Outbound side effects: `act` (the outbox)

`create`/`update`/`link`/`ask` change *state*. An email, a webhook or a deploy
changes the *world* and must not be repeated by a replay or retry. Record the
intent and let the runtime perform it after the commit:

```python
effects.act(
    "notify", key=f"notify:{order_id}:email", payload={"to": email}
)
```

`act` creates a `PendingAction` under the stable id `action:{key}` (a retry or a
branch that reaches the same action produces no second one). The step must
**not** do the I/O; pass `Runtime(dispatcher=...)` and the runtime calls it once
per committed intent, recording `dispatched`/`failed`. See
[patterns — Outbox](patterns.md#outbox-external-side-effects) and
[durability](durability.md).

## The three-layer picture

| Layer | What it is | Who writes it |
| --- | --- | --- |
| **Step** | the reaction: guard → LLM/calc → `effects.*` or a return value | the application (you) |
| **Effects** | the stated change-set (creates/updates/links/questions), scoped to the run | you, through the injected `effects` |
| **Patch** | the *compiled* operations the runtime applies as one commit | the runtime |

Nothing is applied until the step returns — **atomicity is structural** (§41),
no rollback machinery. Events, validation against `produces`, and trace
reads/writes/relations all derive from the same compiled ops.

## Why effects are injected

The effects slot is step-scoped: the runtime pushes a fresh one before each
execution and pops it after (a contextvar) — safe under parallel steps. A class
step names it as a parameter (`effects: Effects`); a function step reaches it
through `call.effects`. You can build handles across statements (`evidence`
created above is linked below), which is why call sites read "about artifacts",
not "about ids".

## Where `Patch` still shows up

- **`Agent.run`** — the escape hatch for custom agents that assemble a
  change-set by hand.
- **Recipes** (`fan_out_sources`, `materialize_doc`) and `StatusMachine` write
  into the slot; the tool loop (`ToolUse`/`ToolUseHITL`) and HITL
  (`effects.ask`) are effects too.
- **Tests and advanced assembly** may still build a `Patch`; ordinary steps
  should not need it.

## The rule of thumb

```text
guard → decide → describe (effects) → return None
```

If you find yourself writing `Patch()` inside a step — stop and write effects;
the runtime does the compiling.

## The function form (`@step`) — same authoring surface

A decorated function receives `call` and uses `call.effects`:

```python
from reactifact import StepCall, step

@step(produces=Answer, consumes=Question)
async def answer_turn(call: StepCall) -> None:
    question = call.trigger
    answer = call.effects.create(
        Answer(text=...), id=f"answer:{question.id}"
    )
    answer.link("derived_from", question)
```

Returning a model / list of models / `Patch` / `None` is compiled by the
runtime, so short steps stay one-liners:
`def f(call): return Answer(...)`.

## Which style to reach for

The subclass and `@step` function are the two canonical styles — pick subclass
when the step has logic worth naming as a class, `@step` for a short one-off.
Overriding `Agent.run(self, event, context) -> Patch` remains a low-level
escape hatch, not an everyday authoring style.
