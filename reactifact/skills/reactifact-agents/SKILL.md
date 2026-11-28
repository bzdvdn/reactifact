---
name: reactifact-agents
description: Author reactifact agents deeply — Agent/Step/Read/Consume contracts, scope and cross-turn history, lifecycle forms (on_create, on_update, on_stale, after_delete), the advanced Read variants (idempotent, join, correlated, absent), Effects and return-style instructions, ids and idempotency, provenance links, side-effect-only steps, triggers, priority and concurrency. Use when writing or reviewing the Agent/Step/Read/effects layer of a reactifact pipeline, when wiring multiple agents that must run in a particular order or only when a set of artifacts exists, or when an agent runs too often, too rarely, or produces duplicates. Not for model calls or prompts (`reactifact-llm`) or retrieval (`reactifact-rag`).
---

# Authoring agents in reactifact

An agent is a bundle of **consumes** (what wakes it) and **steps** (the
transformations it runs). Each step declares its own `reads`/`produces`
contract; the agent owns `consumes`. There is no group-level `run()` to
override in the common case — you declare the contract and implement each
step's transformation.

```python
# not-run: illustrative — answer/Question are defined below
agent = Agent("answerer", consumes=[Consume(Question)], steps=[answer])
```

`@agent` is the one-function shortcut; `Agent(...)` is the constructor for
several steps. Subclassing `Agent` is optional sugar for stateful
class-oriented code, not the default. Overriding `run()` with no `consumes` is
the imperative escape hatch, not the ordinary style.

| You need | Use |
| --- | --- |
| one transformation with typed inputs | `@step(reads=..., produces=...)` |
| a one-function agent | `@agent(consumes=...)` |
| several steps sharing policy or `self` state | `Agent(consumes=..., steps=[...])` |
| many agents with seed/answer models | `Pipeline(agents=[...])` + `PipelineRunner` |
| a conversation across turns | `Chat(agents=..., seed_type=..., answer_type=...)` |

## A step reads its inputs and writes effects

A `@step` function declares its inputs with `Read` (or with typed parameters,
which derive them) and returns the output. The same typed parameters work in a
class step's `run()`: `T`, `Artifact[T]`, `list[T]`, `Latest[T]`, `Context`,
`Effects`, `Progress`, `CurrentRequest`, `Event`. A `Read` never wakes the
agent — `Agent.consumes` is the wake contract.

When `reads=` is explicit, typed artifact params missing from it are added as
**read-only inputs** automatically. `Annotated[T, When(...)]` conditions a
type; `Annotated[T, Wakes(False)]` marks one read-only input when the agent's
consumes are derived from the step.

Prefer a returned model, `Emit`/`Ensure`/`Edit`/`Drop`/`Relate`, or
`self.progress(...)` over reaching for `Context`. Take `effects: Effects`
only when the write needs an imperative handle or a batch of conditional
effects; a single `inv: StepInvocation` parameter is the low-level integration escape
hatch, not the ordinary style.

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
    step,
)


class Question(BaseModel):
    text: str


class Evidence(BaseModel):
    text: str
    score: float = 0.0


class Answer(BaseModel):
    text: str


@step(reads=Question, produces=Evidence)
async def scout(question: Question) -> Evidence:
    return Evidence(text=f"about {question.text}", score=0.9)


@step(reads=Evidence, produces=Answer)
async def answer(
    question: Question,          # read-only: not in this step's explicit reads
    evidence: Artifact[Evidence],
) -> Emit:
    return Emit(
        Answer(text=question.text.upper()),
        links=(("supported_by", evidence),),
    )


qa = Agent(
    "qa",
    consumes=[Consume(Question), Consume(Evidence)],
    steps=[scout, answer],
)


ctx = Context(resources=RuntimeResources())
ctx.create(Question(text="hello"))
asyncio.run(Runtime(ctx, agents=[qa]).arun())

answer_artifact = ctx.latest(Answer)
assert answer_artifact is not None
assert ctx.related(answer_artifact.id, "supported_by")  # provenance exists
```

`question: Question` is not in `answer`'s `reads`; with an explicit `reads=`
the typed artifact parameter is added as a **read-only input**. It feeds the
step without waking it, so `answer` fires on `Evidence`, not on the bare
`Question`. Use `Annotated[Question, Wakes(False)]` when the consume set is
derived and the input must be spelled out as read-only.

## Scope and cross-turn history

`scope` restricts typed inputs to the trigger's correlation value. It works as a
class attribute, in the `Step` constructor, and in `@step(scope=...)`:

```python
# not-run: illustrative
@step(reads=(Question, Doc), produces=Answer, scope="query_id")
async def compose(question: Question, doc: Doc) -> Answer:
    return Answer(text=f"{question.text}: {doc.text}")
```

History is the exception: `Annotated[list[Artifact[Answer]], Unscoped()]` reads
every Answer in the session, ignoring the step's scope. Use `list[Answer]` for
data, `list[Artifact[Answer]]` for handles, and add `Wakes(False)` when history
is a read rather than a wake reason. `Latest[T]` composes the same way:
`Annotated[Latest[Digest], Unscoped()]`.

## Step reads vs agent consumes

`Read` is a step input; `Consume` is the agent wake contract. Both share
`artifact_type`/`condition`/`event_types`/`include_subclasses`; `Read` adds
`required`, `Consume` adds `wakes`/`debounce`.

| Read form | Meaning |
| --- | --- |
| `Read(T)` | required input; a missing artifact is a `MissingInputError` |
| `Read(T, wakes=False)` | read-only input: never wakes a derived agent on its own |
| `T | None` (typed param) | optional input; a missing artifact resolves to `None` |
| `Read.on_create(T)` / `on_update` / `on_stale` | narrow the input to one lifecycle event |
| `Read.after_delete(T)` | the only delete read; take `event: Event` or `trigger`, never `T` |
| `Read.by_status(T, "ready")` / `Read.by_field(T, "kind", "x")` / `Read.where(T, fn)` | input condition |
| `Read.idempotent(T, id_of=...)` | only while the derived target id is absent |
| `Read.join(A, B, key=...)` | a correlated group of inputs |
| `Read.correlated(key=..., require=[...], forbid=[...])` | a custom correlated group |
`wakes` only controls whether an explicit read can feed a *derived* agent
wake set; it never means "optional". Optional inputs come from the signature
(`T | None`), and `Read.required` is a deprecated alias for `wakes`.


| Consume form | Wakes when |
| --- | --- |
| `Consume(T)` | a `T` is created or updated |
| `Consume(T, wakes=False)` | never wakes; feeds the agent inputs only |
| `Consume(T, debounce=True)` | collapses many events in one generation into one run |
| `Consume.on_create(T)` / `on_update` / `on_stale` / `on_delete(T)` | one lifecycle event |
| `Consume.by_status(T, "ready")` / `by_field(T, "kind", "x")` | `T` matches a field |
| `Consume.by_base(Base)` | also wake on subclasses of `Base` |
| `IdempotentConsume(T, id_of=...)` | `T` while the derived id is absent |
| `JoinConsume(A, B, key=lambda d: d.thread_id)` | artifacts of **all** listed types exist sharing a key |
| `AbsentConsume(T, absent_type=U, key=...)` | a `T` exists with **no** matching `U` |
| `CorrelatedConsume(key=..., require=[...], forbid=[...])` | a full group exists and is unblocked |

Typed-parameter annotations fine-tune either side: `Annotated[T, When(fn)]`
conditions an input, `Annotated[T, Wakes(False)]` marks a derived read-only
input, `Annotated[T, Unscoped()]` reads across turns, and
`Scope(field, owner=Type, key=fn)`/`scope=` correlates inputs.

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
| `human.ask(text, kind=...)` / `human.resume(...)` | pause for a human (HITL); inject `human: Human` |

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
@step(reads=Question, produces=(), deletes=(Answer,))
async def cleanup(context: Context, effects: Effects) -> None:
    for artifact in context.list_artifacts(Answer):
        effects.delete(artifact)
```

`@step(reads=Question, produces=())` is the function form. `deletes=` is
declarative (contracts show it); the runtime does not gate deletes on it. For
the common "delete this turn's scratch when the terminal artifact exists",
reuse `ScopedCleanup` from `reactifact.recipes` instead of hand-writing the
loop.

Full details, ids and HITL: `references/effects.md`.

## Layout: agents first

Put public capabilities in `agents/`, one agent per file. A one-step agent is
an `@agent` function — no separate `Step` class or `steps/` folder:

```text
app/
  agents/
    router.py      # @agent: UserMsg -> *Problem
    answer.py      # @agent: Question -> Answer
  models.py
```

Keep a `Step` class or a private `_steps.py` only when one agent genuinely has
several stages, shared state, or an imperative integration. The public import
should still be the `Agent`/`@agent`, not the step.

With `REACTIFACT_DEV=1` (or `REACTIFACT_ENV=dev`), constructing
`Agent(steps=...)` without `consumes` emits a warning: the runtime can derive
the wake set from step reads, but the agent should state it.

## Agents and structured pipelines

`Agent` is the user-facing scheduling unit. It owns `consumes` (wake); each
step owns `reads` (input) and `produces`. `Read` is the input descriptor:

```python
# not-run: illustrative
@step(
    reads=(Read.on_create(Query), Read(Evidence)),
    produces=Answer,
)
async def handle(
    context: Context,
    query: Query,
    evidence: Evidence | None = None,
) -> Answer: ...
```

For delete-only cleanup, use `Read.after_delete(Scratch)` and take `event: Event`
in `run()` — the deleted artifact has no data to read.

For one function, prefer `@agent`:

```python
from pydantic import BaseModel
from reactifact import Consume
from reactifact.agent import agent


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@agent(consumes=[Consume(Question)])
async def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())
```

`answer` is an `Agent`; mount it in a `Pipeline` or `Runtime`. Use the explicit
form when the work needs several steps:

```python
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Pipeline,
    PipelineRunner,
    RetryPolicy,
    step,
)


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(reads=Question, produces=Answer)
async def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())


QA = Pipeline(
    name="qa",
    agents=[
        Agent(
            "answer",
            steps=[answer],
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

- `Agent(consumes=[...], steps=[...])` owns the wake contract and
  retry/timeout/fallback/priority policy; it **is** the scheduling unit.
  Spell `consumes` out even though the runtime can derive it from the steps.
- `Pipeline(agents=[...])` is declarative structure; `agents=` takes `Agent` or an agent source like `Route`,
  `Pipeline.agents` expands agents and `Route.agents()` for `Runtime`.
- `PipelineRunner` + `RunConfig` own resources/budget/tracing and run;
  `Chat(pipeline=..., config=RunConfig(...))` reuses both for a conversation.
- An agent is a named scheduling unit, not an execution barrier: `Runtime` still schedules
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
  and pass it directly: `Runtime(agents=[route, ...])`; `validate_contracts`
  warns about `ambiguous_producer` outside a route.
- **Idempotency**: agents may re-wake on an update. Use stable ids with
  `create_once`/`upsert` (or return-style `Ensure`/`Emit`), or gate with
  `AbsentConsume`, so a second wake is a no-op rather than a duplicate.

## Degradation at the agent boundary

When an agent's main step can fail on an external dependency, declare a fallback
instead of copying try/except into the step:

```python
# not-run: illustrative
Agent(
    "answer_builder",
    consumes=[Consume(Answer)],
    steps=[BuildAnswer()],
    fallback=FallbackAnswer("Не удалось собрать ответ."),
    fallback_on=(ConnectionError, TimeoutError),
)
```

The fallback itself is a small step using the return-style primitive:

```python
# not-run: illustrative
from typing import Any

from reactifact import Artifact, Ensure, step


def fallback_answer(text: str):
    @step(produces=Answer)
    async def _fallback(trigger: Artifact[Any] | None) -> Ensure:
        qid = getattr(trigger.data, "query_id", "unknown") if trigger else "unknown"
        return Ensure(Answer(query_id=qid, text=text), id=f"answer:{qid}")

    return _fallback


fallback = fallback_answer("Не удалось собрать ответ.")
```

`fallback=` accepts the returned `Step`; a closure keeps configuration out of
the artifact contract.

`fallback=` runs once after retries, in a fresh effects slot; the failed
attempt is discarded. It accepts a `Step`, not an `Agent`. `fallback_on` /
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
instead of becoming an artifact; a step or agent invoked directly instead of
through `consumes`; a step and its consumer coupled by a shared global instead
of an artifact type; an agent that fires once per history artifact (missing
`wakes=False`); duplicates on re-wake (missing stable id).

## Where to look

- `references/consume.md` — every `Read` and `Consume` form with examples.
- `references/effects.md` — every effect, ids, atomicity, HITL.
- `docs/en/concepts.md`, `docs/en/effects.md`, `docs/en/scheduler-semantics.md`.
