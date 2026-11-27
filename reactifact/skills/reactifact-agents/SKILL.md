---
name: reactifact-agents
description: Author reactifact agents deeply — Step/Consume contracts, scope and cross-turn history, the Consume lifecycle classes and variants (by_status, by_field, Join, Absent, Correlated), Effects and return-style instructions, ids and idempotency, provenance links, side-effect-only steps, triggers, priority and concurrency. Use when writing or reviewing the produce/consume/effects layer of a reactifact pipeline, when wiring multiple agents that must run in a particular order or only when a set of artifacts exists, or when an agent runs too often, too rarely, or produces duplicates.
---

# Authoring agents in reactifact

An agent is a bundle of **consumes** (what wakes it) and **produces** (the
transformations it may apply). There is no `run()` to override in the common
case — you declare the contract and implement each produce's effects.

```python
# not-run: illustrative — Answerer/Question/Evidence are defined below
agent = create_step_group("answerer", steps=[Answerer()])
```

`create_step_group` is the constructor-style builder; subclassing `StepGroup` is only
needed for the imperative escape hatch (overriding `run()` with no `consumes`).

## A produce reads its inputs and writes effects

A class step gets typed inputs + `effects: Effects`. A `@step` function uses the
same typed parameters (`T`, `Artifact[T]`, `list[T]`, `Latest[T]`, `Context`,
`Effects`, `Progress`, `CurrentRequest`, `Event`), or the legacy single
`call: StepCall` with
`call.trigger`/`call.context`/`call.effects`/`call.inputs`/`call.request`.
Typed parameters derive the step's `consumes`; when `consumes` is explicit, it is
the wake set and typed artifact params missing from it are added as read-only
inputs automatically. `Annotated[T, When(...)]` conditions a type;
`Annotated[T, Wakes(False)]` marks one read-only input when `consumes` is
derived.

Prefer a returned model, `Emit`/`Ensure`/`Edit`/`Drop`/`Relate`, or
`self.progress(...)` over reaching for `Context`/`call.context`. The low-level
`call` form is for an imperative integration, not for ordinary steps.

```python
import asyncio
from pydantic import BaseModel
from reactifact import (
    StepGroup, Artifact, Consume, Context, Effects, Runtime, RuntimeResources, Step,
)


class Question(BaseModel):
    text: str


class Evidence(BaseModel):
    text: str
    score: float = 0.0


class Answer(BaseModel):
    text: str


class Scout(Step):
    consumes = (Question,)
    produces = (Evidence,)

    async def run(self, question: Question, effects: Effects) -> None:
        effects.create(Evidence(text=f"about {question.text}", score=0.9))


class Answerer(Step):
    consumes = (Evidence, Consume(Question, wakes=False))
    produces = (Answer,)

    async def run(
        self,
        question: Question,
        evidence: Artifact[Evidence],
        effects: Effects,
    ) -> None:
        answer = effects.create(Answer(text=question.text.upper()))
        answer.link("supported_by", evidence)


class Pipeline(StepGroup):
    steps = [Scout(), Answerer()]


ctx = Context(resources=RuntimeResources())
ctx.create(Question(text="hello"))
asyncio.run(Runtime(ctx, groups=[Pipeline()]).arun())

answer = ctx.latest(Answer)
assert answer is not None
assert ctx.related(answer.id, "supported_by")  # provenance exists
```

The `wakes=False` on the `Question` consume is the key idea: it feeds the
agent's inputs but does not make the agent run. Without it the answerer would
fire the moment the question exists — before any evidence — and produce an
ungrounded answer.

## Scope and cross-turn history

`scope` restricts typed inputs to the trigger's correlation value. It works as a
class attribute, in the `Step` constructor, and in `@step(scope=...)`:

```python
# not-run: illustrative
@step(consumes=Question, produces=Answer, scope="query_id")
async def answer(question: Question) -> Answer: ...

class Compose(Step):
    consumes = (Question, Doc)
    produces = (Answer,)
    scope = "query_id"
```

History is the exception: `Annotated[list[Artifact[Answer]], Unscoped()]` reads
every Answer in the session, ignoring the step's scope. Use `list[Answer]` for
data, `list[Artifact[Answer]]` for handles, and add `Wakes(False)` when history
is a read rather than a wake reason. `Latest[T]` composes the same way:
`Annotated[Latest[Digest], Unscoped()]`.

## Consume variants (the whole toolkit)

| Form | Wakes when |
| --- | --- |
| `Consume(T)` | a `T` is created or updated |
| `Consume(T, condition=lambda a: ...)` | a matching `T` appears (condition on the `Artifact`) |
| `Annotated[T, Wakes(False)]` | reads `T` as input, never wakes — needed when `consumes` is derived (an explicit `consumes` already makes unlisted typed params reads) |
| `IdempotentConsume(T, id_of=...)` | wakes on `T` only while `id_of(artifact)` is absent (the declarative idempotency guard) |
| `Annotated[T, When(fn)]` | wakes when `fn(data)` is true |
| `Scope(field, owner=Type, key=fn)` / `scope=` | correlate typed inputs; owner matches by artifact id, `key` overrides extraction; pass `scope=` to `@step`/`Step(...)` |
| `Annotated[T, Unscoped()]` | read T across every turn, not only the current `scope` |
| `OnCreate(T)` / `OnUpdate(T)` / `OnDelete(T)` / `OnStale(T)` | one lifecycle event only; `OnDelete` takes `event`/`Trigger`, not typed data |
| `Consume.by_status(T, "ready")` | `T.status == "ready"` (has `status` field) |
| `Consume.by_field(T, "kind", "x")` | `T.kind == "x"` |
| `JoinConsume(A, B, key=lambda d: d.thread_id)` | artifacts of **all** of A and B exist sharing a key |
| `AbsentConsume(T, absent_type=U, key=...)` | a `T` exists with **no** matching `U` |
| `CorrelatedConsume(key=..., require=[...], forbid=[...])` | a full group exists and is unblocked |

`wakes=False` reads input without waking. `debounce=True` collapses many events
in one generation (a fan-out creating five `Evidence`) into one run.

Full details and examples: `references/consume.md`.

## Effects

Effects are the only way to change state. They are compiled into one atomic
patch. `create` returns a `Handle` you can link immediately.

| Call | Effect |
| --- | --- |
| `create(data, id=None) -> Handle` | create an artifact |
| `create_once(data, id=...)` | create unless that id exists (idempotent re-wakes) |
| `upsert(data, id=...)` | create or replace by id |
| `update(artifact, **fields)` | patch fields |
| `link(source, relation, target)` / `unlink(...)` | provenance edge |
| `delete(artifact)` | remove |
| `human.ask(text, kind=...)` / `human.resume(...)` | pause for a human (HITL); inject `Human` or use `call.human` |

Return-style equivalents (preferred for short steps): `Emit` (create, or
refresh an existing id — the `upsert` form), `Ensure` (create once, by id or
`source=`/`prefix=`), `Edit`, `Drop`, `Relate`. `Ensure` is a no-op when the
artifact already exists; `Emit` refreshes it.

For outbound side effects use `outbox.act(...)`; never perform the external I/O
inside the step. `TurnContextStep` (in `reactifact.recipes.conversation`) is the
ready-made conversation artifact when several steps need the same window.

Side-effect-only steps declare no creates honestly:

```python
# not-run: illustrative
class CleanupTurn(Step):
    produces = ()
    deletes = (Answer,)
    consumes = (Question,)

    async def run(self, context: Context, effects: Effects) -> None:
        for artifact in context.list_artifacts(Answer):
            effects.delete(artifact)
```

`@step(consumes=Question, produces=())` is the function form. `deletes=` is
declarative (contracts show it); the runtime does not gate deletes on it.

Full details, ids and HITL: `references/effects.md`.

## Scheduling and ordering

- **Order comes from data, not edges.** `A → B` is expressed as "B consumes
  what A produces". If B must wait for a *set*, use `JoinConsume`; if it must
  wait for *absence*, use `AbsentConsume`.
- **`priority`** (lower runs earlier in a generation) and
  **`concurrency_limit`** tune scheduling but should not carry correctness —
  the fixpoint does. Avoid relying on intra-generation order; make the
  dependency an artifact instead.
- **Mutually exclusive cases**: when one artifact field selects one of several
  downstream groups, build them with `recipes.Route(on=..., key=..., cases=...)`
  instead of hand-written conditions; `validate_contracts` warns about
  `ambiguous_producer` outside a route.
- **Idempotency**: agents may re-wake on an update. Use stable ids with
  `create_once`/`upsert` (or return-style `Ensure`/`Emit`), or gate with
  `AbsentConsume`, so a second wake is a no-op rather than a duplicate.

## When the low-level API is justified

Use `StepGroup.run(event, context) -> Patch` or direct `Context` queries only
when the task is genuinely imperative:

- a third-party integration whose behaviour cannot be expressed as typed
  inputs/effects;
- a custom scheduler/transport that needs to build a `Patch` itself;
- a read pattern that is not a type or a scope (e.g. a cross-type aggregate the
  context builder already computes).

If you reach for `Context` to read a known artifact type, check first whether
`Annotated[T, ...]`, `Latest[T]`, `Unscoped()` or a `JoinConsume` expresses the
same intent. If you reach for `effects.create` in a short step, check whether a
returned model/`Emit` is enough.

## Reviewing existing agent code

Look for: mutation of `context`/`.data`; a produce returning a value as its
result; `agent.run()`/`agent.produce()` called directly; a producer and
consumer coupled by a shared global instead of an artifact type; an agent that
fires once per history artifact (missing `wakes=False`); duplicates on
re-wake (missing stable id).

## Where to look

- `references/consume.md` — every `Consume` form with examples.
- `references/effects.md` — every effect, ids, atomicity, HITL.
- `docs/en/concepts.md`, `docs/en/effects.md`, `docs/en/scheduler-semantics.md`.
