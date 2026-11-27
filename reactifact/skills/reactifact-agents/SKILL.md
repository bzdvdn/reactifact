---
name: reactifact-agents
description: Author reactifact agents deeply — Step/Consume contracts, scope and cross-turn history, the Consume lifecycle classes and variants (by_status, by_field, Join, Absent, Correlated), Effects and return-style instructions, ids and idempotency, provenance links, side-effect-only steps, triggers, priority and concurrency. Use when writing or reviewing the step/consume/effects layer of a reactifact pipeline, when wiring multiple agents that must run in a particular order or only when a set of artifacts exists, or when an agent runs too often, too rarely, or produces duplicates. Not for model calls or prompts (`reactifact-llm`) or retrieval (`reactifact-rag`).
---

# Authoring agents in reactifact

An agent is a bundle of **consumes** (what wakes it) and **steps** (the
transformations it runs). Each step declares its own `produces` contract. There
is no group-level `run()` to override in the common case — you declare the
contract and implement each step's effects.

```python
# not-run: illustrative — Answerer/Question/Evidence are defined below
agent = Agent("answerer", steps=[Answerer()])
```

`Agent(...)` is the constructor for every group; do not subclass it in app
code. Overriding `run()` with no `consumes` is the imperative escape hatch,
not the ordinary style.

## A step reads its inputs and writes effects

A class step gets typed inputs + `effects: Effects`. A `@step` function uses the
same typed parameters (`T`, `Artifact[T]`, `list[T]`, `Latest[T]`, `Context`,
`Effects`, `Progress`, `CurrentRequest`, `Event`), or the legacy single
`call: StepCall` with
`call.trigger`/`call.context`/`call.effects`/`call.inputs`/`call.request`.
Typed parameters derive the step's `reads`; when `reads` is explicit, it is
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
    Agent,
    Artifact,
    Consume,
    Context,
    Emit,
    Runtime,
    RuntimeResources,
    Step,
)


class Question(BaseModel):
    text: str


class Evidence(BaseModel):
    text: str
    score: float = 0.0


class Answer(BaseModel):
    text: str


class Scout(Step):
    reads = (Question,)
    produces = (Evidence,)

    async def run(self, question: Question) -> Evidence:
        return Evidence(text=f"about {question.text}", score=0.9)


class Answerer(Step):
    reads = (Evidence,)  # Question is auto-added as a read-only input
    produces = (Answer,)

    async def run(
        self,
        question: Question,
        evidence: Artifact[Evidence],
    ) -> Emit:
        return Emit(
            Answer(text=question.text.upper()),
            links=(("supported_by", evidence),),
        )


Pipeline = Agent("pipeline", steps=[Scout(), Answerer()])


ctx = Context(resources=RuntimeResources())
ctx.create(Question(text="hello"))
asyncio.run(Runtime(ctx, agents=[Pipeline]).arun())

answer = ctx.latest(Answer)
assert answer is not None
assert ctx.related(answer.id, "supported_by")  # provenance exists
```

`question: Question` is not listed in `consumes`; with an explicit consume list
the typed artifact parameter is added as a **read-only input**. It feeds the
step without waking it, so the answerer fires on `Evidence`, not on the bare
`Question`. Use `Consume(Question, wakes=False)` only when you want that input
spelled out in the consume list.

## Scope and cross-turn history

`scope` restricts typed inputs to the trigger's correlation value. It works as a
class attribute, in the `Step` constructor, and in `@step(scope=...)`:

```python
# not-run: illustrative
@step(reads=Question, produces=Answer, scope="query_id")
async def answer(question: Question) -> Answer: ...


class Compose(Step):
    reads = (Question, Doc)
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
| `Annotated[T, Wakes(False)]` | reads `T` as input, never wakes — needed when `reads` is derived (an explicit `reads` already makes unlisted typed params reads) |
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

Every state change is compiled into one atomic patch. You express it as effects
or as a return-style instruction; both land in the same commit. `create`
returns a `Handle` you can link immediately.

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
    reads = (Question,)

    async def run(self, context: Context, effects: Effects) -> None:
        for artifact in context.list_artifacts(Answer):
            effects.delete(artifact)
```

`@step(reads=Question, produces=())` is the function form. `deletes=` is
declarative (contracts show it); the runtime does not gate deletes on it. For
the common "delete this turn's scratch when the terminal artifact exists",
reuse `ScopedCleanup` from `reactifact.recipes` instead of hand-writing the
loop.

Full details, ids and HITL: `references/effects.md`.

## Agents and structured pipelines

`Agent` is the user-facing group of steps. It owns `consumes` (wake); each step
owns `reads` (input) and `produces`. `Read` is the input descriptor:

```python
# not-run: illustrative
class Handler(Step):
    reads = (
        Read.on_create(Query),
        Read(Evidence, required=False),
    )
    produces = (Answer,)
```

For delete-only cleanup, use `Read.after_delete(Scratch)` and take `event: Event`
in `run()` — the deleted artifact has no data to read.

For one function, prefer `@agent`:

```python
from pydantic import BaseModel
from reactifact.agent import agent


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@agent
async def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())
```

`answer` is an `Agent`; mount it in a `Pipeline` or `Runtime`. Use the explicit
form when the work needs several steps:

```python
from pydantic import BaseModel
from reactifact import Consume, Agent, Pipeline, PipelineRunner, RetryPolicy, Step


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Answerer(Step[Answer]):
    async def run(self, question: Question) -> Answer:
        return Answer(text=question.text.upper())


QA = Pipeline(
    name="qa",
    agents=[
        Agent(
            "answer",
            steps=[Answerer()],
            consumes=[Consume(Question)],
            retry=RetryPolicy(max_attempts=2),
            timeout=30,
        ),
    ],
    seed_type=Question,
    answer_type=Answer,
)

result = PipelineRunner(QA).run(Question(text="hello"))
assert result.context.latest(Answer).data.text == "HELLO"
```

- `Agent(steps=[...])` owns the steps and `consumes`/retry/timeout/fallback/
  priority policy; it **is** the scheduling unit.
- `Pipeline(agents=[...], routes=[...])` is declarative structure;
  `Pipeline.agents` expands agents and `Route.agents()` for `Runtime`.
- `PipelineRunner` + `RunConfig` own resources/budget/tracing and run;
  `Chat(pipeline=..., config=RunConfig(...))` reuses both for a conversation.
- An agent is a named group, not an execution barrier: `Runtime` still schedules
  by artifact availability.

For stateful class-oriented code, `Agent` can be subclassed and `@step` applied
to its methods. The method is a step declaration, not a callable: the runtime
binds it to the instance.

```python
# not-run: illustrative
class AnswerAgent(Agent):
    name = "answer"
    consumes = [Consume(Question)]

    def __init__(self, model: str):
        self.model = model
        super().__init__()

    @step(produces=Answer)
    async def answer(self, question: Question) -> Answer:
        return Answer(text=f"{self.model}: {question.text}")
```

Prefer `Agent(consumes=..., steps=[...])` or `@agent` for simple, stateless
agents; use the subclass form when methods need `self` state.

## Scheduling and ordering

- **Order comes from data, not edges.** `A → B` is expressed as "B consumes
  what A produces". If B must wait for a *set*, use `JoinConsume`; if it must
  wait for *absence*, use `AbsentConsume`.
- **`priority`** (lower runs earlier in a generation) and
  **`concurrency_limit`** tune scheduling but should not carry correctness —
  the fixpoint does. `Step.priority` applies when steps go through
  `Runtime(steps=...)`; inside an explicit `Agent`, steps run in list
  order, and `Agent.priority` orders agents within a generation. Avoid
  relying on intra-generation order; make the dependency an artifact instead.
- **Mutually exclusive cases**: when one artifact field selects one of several
  downstream groups, build them with `recipes.Route(on=..., key=..., cases=...)`
  and expand with `Runtime(agents=[*route.agents(), ...])`; `validate_contracts`
  warns about `ambiguous_producer` outside a route.
- **Idempotency**: agents may re-wake on an update. Use stable ids with
  `create_once`/`upsert` (or return-style `Ensure`/`Emit`), or gate with
  `AbsentConsume`, so a second wake is a no-op rather than a duplicate.

## Degradation at the group boundary

When a group's main step can fail on an external dependency, declare a fallback
instead of copying try/except into the step:

```python
# not-run: illustrative
Agent(
    "answer_builder",
    steps=[BuildAnswer()],
    fallback=FallbackAnswer("Не удалось собрать ответ."),
    fallback_on=(ConnectionError, TimeoutError),
)
```

The fallback itself is a small step using the return-style primitive:

```python
# not-run: illustrative
from typing import Any

from reactifact import Artifact, Ensure, Step


class FallbackAnswer(Step):
    produces = (Answer,)

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()

    async def run(
        self,
        trigger: Artifact[Any] | None,
    ) -> Ensure:
        qid = getattr(trigger.data, "query_id", "unknown") if trigger else "unknown"
        return Ensure(Answer(query_id=qid, text=self.text), id=f"answer:{qid}")
```

`fallback=` runs once after retries, in a fresh effects slot; the failed
attempt is discarded. It accepts a `Step`, not a group. `fallback_on` /
`fallback_skip` keep authz, guardrail and artifact-conflict errors from being
silently converted into a normal answer. `Chat(fallback_reply=...)` remains the
last-resort transport fallback when even the fallback step cannot answer.

## When the low-level API is justified

Use `Agent.run(event, context) -> Patch` or direct `Context` queries only
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

Look for: mutation of `context`/`.data`; a step result read by the caller
instead of becoming an artifact; a step or group invoked directly instead of
through `consumes`; a step and its consumer coupled by a shared global instead
of an artifact type; an agent that fires once per history artifact (missing
`wakes=False`); duplicates on re-wake (missing stable id).

## Where to look

- `references/consume.md` — every `Consume` form with examples.
- `references/effects.md` — every effect, ids, atomicity, HITL.
- `docs/en/concepts.md`, `docs/en/effects.md`, `docs/en/scheduler-semantics.md`.
