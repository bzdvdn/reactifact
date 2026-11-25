# The step contract & effects (§24)

One paragraph to keep in your head while reading any example or writing a
step:

> **A step describes what should change by writing effects, or returns its
> output; the runtime compiles the change-set into one atomic patch and commits
> it. You almost never build a `Patch` yourself — it is the runtime's
> transport.**

```python
from reactifact import Artifact, Effects, Human

async def run(
    self,
    doc: Artifact[Document],
    turn: Artifact[Turn],
    effects: Effects,
    human: Human,
) -> None:
    evidence = effects.create(Evidence(...), id="evidence:q1")
    answer = effects.create(Answer(...), id="answer:q1")
    evidence.link("extracted_from", doc)
    answer.link("supported_by", evidence)
    effects.update(turn, status="answered")
    human.ask("Approve the estimate?", kind="approval")   # HITL (§60)
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

When the id or provenance must be explicit, return `Emit` instead of dropping
to `effects`:

```python
from reactifact import Emit

return Emit(
    Answer(text=...),
    id=f"answer:{question.id}",
    links=(("supported_by", evidence),),
)
```

For mutations, return `Edit`, `Drop` or `Relate` instead of calling effects:

```python
from reactifact import Drop, Edit, Relate

return Edit(ticket, status="closed")          # effects.update
return Drop(ticket)                            # effects.delete
return Relate(answer, "supported_by", evidence)
```

Use `effects` for batch/conditional writes, or when the target id is computed at
runtime. Returning `None` means "no changes" (a guard or a branch that did not
apply).

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

## Human and outbox are not effects

`Effects` is the data-write surface. Two non-data collaborators are injected
next to it:

- `Human.ask(...)` / `Human.resume(...)` — HITL, backed by `PendingQuestion`;
- `Outbox.act(...)` — outbound side-effect intent, backed by `PendingAction`.

```python
@step(produces=PendingAction, consumes=Ticket)
def notify(ticket: Ticket, outbox: Outbox) -> None:
    outbox.act("notify", key=f"notify:{ticket.id}", payload={"to": ticket.email})
```

`Outbox.act` creates a `PendingAction` under the stable id `action:{key}` (a
retry or a branch that reaches the same action produces no second one). The step
must **not** do the I/O; pass `Runtime(dispatcher=...)` and the runtime calls it
once per committed intent, recording `dispatched`/`failed`. See
[patterns — Outbox](patterns.md#outbox-external-side-effects) and
[durability](durability.md). `Effects.ask/resume/act` were removed; use `Human`/`Outbox`.

## Progress is neither data nor an effect

`progress: Progress` is the third injected collaborator — an ephemeral UI
signal:

```python
@step(produces=Answer, consumes=Question)
async def answer(question: Question, progress: Progress, llm: LLMProvider | None) -> Answer:
    progress("Считаю…", count=3)
    ...
```

It is a thin view over `Context.announce`, so it publishes a `ProgressEvent`
immediately and never enters the commit. A step that only announces and then
calls an LLM or tools does not need `Context` at all. When the status must
survive a replay or appear in the run's receipt, model it as a status artifact
(`recipes.status.StatusMachine`) instead.

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

- **`StepGroup.run`** — the escape hatch for custom agents that assemble a
  change-set by hand.
- **Recipes** (`fan_out_sources`, `materialize_doc`) and `StatusMachine` write
  into the slot; the tool loop (`ToolUse`/`ToolUseHITL`) and HITL
  (`human.ask`) are interaction, not data writes.
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

## Provenance is a query

Hand-written `link` calls are the explicit form. Two shortcuts keep the common
case short without hiding the edges:

```python
from reactifact import CALCULATED_FROM

@step(produces=Variance, consumes=Spend, provenance=CALCULATED_FROM)
def compute_variance(spend: Spend) -> Variance:
    return Variance(actual=spend.total, ...)
```

`provenance=` links every artifact the step **actually resolved** (not the
whole `consumes` working set) to each artifact it creates. Import the names
from `reactifact` (`DERIVED_FROM`, `SUPPORTED_BY`, `CALCULATED_FROM`, …) instead
of spelling strings; a step whose `produces` is inferred from its return
annotation gets `DERIVED_FROM` by default. An explicit `link` between the same
pair wins, so the shortcut never overrides deliberate provenance.

Reading provenance is a query too:

```python
context.sources(answer)            # ['transactions.csv', 'budget.csv', 'policy.md']
for link in context.why(answer):   # breadth-first over outgoing edges
    print("  " * link.depth, link.relation, type(link.artifact.data).__name__)
```

`why()` follows `supported_by` / `calculated_from` / … edges breadth-first;
pass `relations=(...)` to restrict the walk and `max_depth=` to stop it.
Use `SUPPORTED_BY` only for
answers/claims; compute steps are `CALCULATED_FROM` or `DERIVED_FROM`.

## Which style to reach for

The subclass and `@step` function are the two canonical styles — pick subclass
when the step has logic worth naming as a class, `@step` for a short one-off.
Overriding `StepGroup.run(self, event, context) -> Patch` remains a low-level
escape hatch, not an everyday authoring style.
