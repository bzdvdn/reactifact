---
name: reactifact-agents
description: Author reactifact agents deeply — Produce/Consume contracts, the Consume variants (by_status, by_field, Join, Absent, Correlated), Effects (create/update/link/upsert/delete/ask), ids and idempotency, provenance links, triggers, priority and concurrency. Use when writing or reviewing the produce/consume/effects layer of a reactifact pipeline, when wiring multiple agents that must run in a particular order or only when a set of artifacts exists, or when an agent runs too often, too rarely, or produces duplicates.
---

# Authoring agents in reactifact

An agent is a bundle of **consumes** (what wakes it) and **produces** (the
transformations it may apply). There is no `run()` to override in the common
case — you declare the contract and implement each produce's effects.

```python
# not-run: illustrative — Answerer/Question/Evidence are defined below
agent = create_agent(
    "answerer",
    consumes=[Consume(Evidence), Consume(Question, wakes=False)],
    produces=[Answerer()],
)
```

`create_agent` is the constructor-style builder; subclassing `Agent` is only
needed for the imperative escape hatch (overriding `run()` with no `consumes`).

## A produce reads its inputs and writes effects

A produce gets a `ProduceCall`: `call.trigger` (the waking artifact),
`call.context` (the graph), `call.effects` (the change set), and the collected
`call.inputs` (every artifact this agent's consumes matched). Prefer
`call.context.latest(T)` when you know the type you want; use `call.inputs`
when several artifacts feed one produce. `recipes.find` / `find_all` pick a
typed artifact out of `inputs`.

```python
import asyncio
from pydantic import BaseModel
from reactifact import (
    Consume, Context, Produce, Runtime, RuntimeResources, create_agent,
)


class Question(BaseModel):
    text: str


class Evidence(BaseModel):
    text: str
    score: float = 0.0


class Answer(BaseModel):
    text: str


class Scout(Produce[Evidence]):
    artifact_type = Evidence

    async def produce(self, call):
        call.effects.create(
            Evidence(text=f"about {call.trigger.data.text}", score=0.9)
        )


class Answerer(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call):
        question = call.context.latest(Question)
        evidence = call.trigger                 # the Artifact that woke us (Evidence)
        answer = call.effects.create(Answer(text=question.data.text.upper()))
        answer.link("supported_by", evidence)   # Handle -> Artifact, no id needed


agents = [
    create_agent("scout", consumes=[Consume(Question)], produces=[Scout()]),
    create_agent(
        "answerer",
        # wake on the evidence, read the question without re-waking on it
        consumes=[Consume(Evidence), Consume(Question, wakes=False)],
        produces=[Answerer()],
    ),
]

ctx = Context(resources=RuntimeResources())
ctx.create(Question(text="hello"))
asyncio.run(Runtime(ctx, agents=agents).arun())

answer = ctx.latest(Answer)
assert answer is not None
assert ctx.related(answer.id, "supported_by")  # provenance exists
```

The `wakes=False` on the `Question` consume is the key idea: it feeds the
agent's inputs but does not make the agent run. Without it the answerer would
fire the moment the question exists — before any evidence — and produce an
ungrounded answer.

## Consume variants (the whole toolkit)

| Form | Wakes when |
| --- | --- |
| `Consume(T)` | a `T` is created or updated |
| `Consume(T, condition=lambda a: ...)` | a matching `T` appears (condition on the `Artifact`) |
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
| `ask(text, kind=...)` / `resume(...)` | pause for a human (HITL) |

Full details, ids and HITL: `references/effects.md`.

## Scheduling and ordering

- **Order comes from data, not edges.** `A → B` is expressed as "B consumes
  what A produces". If B must wait for a *set*, use `JoinConsume`; if it must
  wait for *absence*, use `AbsentConsume`.
- **`priority`** (higher runs first in a generation) and
  **`concurrency_limit`** tune scheduling but should not carry correctness —
  the fixpoint does. Avoid relying on intra-generation order; make the
  dependency an artifact instead.
- **Idempotency**: agents may re-wake on an update. Use stable ids with
  `create_once`/`upsert`, or gate with `AbsentConsume`, so a second wake is a
  no-op rather than a duplicate.

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
