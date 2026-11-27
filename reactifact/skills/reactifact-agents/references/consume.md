# Reads and consumes reference

Two contracts share the same selector fields but answer different questions:

- **`Read`** — what a *step* needs as input (`required`, lifecycle event,
  condition). It never wakes an agent.
- **`Consume`** — what wakes an *agent* (`Agent.consumes`). It also contributes
  the agent's inputs.

Both accept `artifact_type`, `condition`, `event_types` and
`include_subclasses`; `Read` adds `required`, `Consume` adds `wakes` and
`debounce`. An artifact type, a `Read` or a `Consume` is accepted wherever a
list is expected.

## Step inputs: `Read`

| Form | Meaning |
| --- | --- |
| `Read(T)` | required input |
| `Read(T, required=False)` | optional input |
| `Read.on_create(T)` / `on_update` / `on_stale` | one lifecycle event |
| `Read.after_delete(T)` | only delete read; take `event: Event`/`trigger`, never `T` |
| `Read.by_status(T, "ready")` / `by_field(T, "kind", "x")` / `where(T, fn)` | condition |
| `Read.idempotent(T, id_of=...)` | only while the derived target id is absent |
| `Read.join(A, B, key=...)` | correlated group of inputs |
| `Read.correlated(key=..., require=[...], forbid=[...])` | custom correlated group |

```python
# not-run: illustrative
from reactifact import Read, step

@step(
    reads=(
        Read.on_create(Query),
        Read(Evidence, required=False),
    ),
    produces=Answer,
)
async def handle(query: Query) -> Answer: ...
```

For delete-only work, `Read.after_delete(Scratch)` is the one delete read:

```python
# not-run: illustrative
from reactifact import Event, Read, step

@step(reads=Read.after_delete(Scratch), produces=())
async def after_scratch_deleted(event: Event) -> None: ...
```

## Agent wake-ups: `Consume`

`Consume(T)` fires when a `T` is created or updated.

```python
# not-run: illustrative
from reactifact import Consume

Consume(Question, condition=lambda a: len(a.data.text) > 0)
Consume.by_status(Report, "approved")          # Report.status == "approved"
Consume.by_field(Ticket, "severity", "high")   # Ticket.severity == "high"
Consume.by_base(ChatMessage)                   # also subclasses
```

`wakes=False` feeds the agent inputs without making it run:

```python
# not-run: illustrative
consumes = [
    Consume(Evidence),               # wake: run when evidence exists
    Consume(Question, wakes=False),  # input only: don't run per question
]
```

`debounce=True` collapses many same-generation events into one run — a fan-out
creating five `Evidence` artifacts wakes once. Read `context.list_artifacts(T)`
for the full set rather than relying on `trigger` for "what changed".

## Lifecycle forms

`Consume(T)` covers create + update. Narrow it with the lifecycle classmethods;
the old `OnCreate`/`OnUpdate`/`OnDelete`/`OnStale` classes remain importable
compatibility aliases, not the canonical spelling.

```python
# not-run: illustrative
from reactifact import Consume

Consume.on_create(T)   # only a new id
Consume.on_update(T)   # only an existing id / changed data
Consume.on_delete(T)   # deleted; use event/trigger, the data is gone
Consume.on_stale(T)    # a recorded dependency got a newer version
```

The step-side equivalents are `Read.on_create`, `Read.on_update`,
`Read.on_stale` and `Read.after_delete`.

## Scope and history

A step `scope` (class attribute or `@step(scope=...)`/`Step(..., scope=...)`)
filters typed inputs to artifacts carrying the trigger's correlation value.
`Annotated[T, Unscoped()]` opts one parameter out of that filter, which is how a
step reads history across turns:

```python
# not-run: illustrative
from typing import Annotated
from reactifact import Artifact
from reactifact.agent import Latest, Unscoped, Wakes

async def run(
    question: Question,
    answers: Annotated[list[Artifact[Answer]], Unscoped()],
    digest: Annotated[Latest[Digest], Unscoped()],
    # read history without waking on every Answer:
    # Annotated[list[Artifact[Answer]], Unscoped(), Wakes(False)]
) -> None: ...
```

Use `list[T]` for data, `list[Artifact[T]]` for handles.

## Joining several artifacts

`Read.join(...)` (step input) and `JoinConsume(...)` (agent wake) fire once
artifacts of every listed type exist sharing a correlation key, and feed all of
them as inputs:

```python
# not-run: illustrative
from reactifact import Read
from reactifact.consume import JoinConsume

# step input
Read.join(
    Report,
    PendingQuestion,
    key=lambda d: d.thread_id,
    part_conditions={PendingQuestion: lambda d: d.answered},
)

# agent wake-up
consumes = [
    JoinConsume(
        Report,
        PendingQuestion,
        key=lambda d: d.thread_id,
        part_conditions={PendingQuestion: lambda d: d.answered},
    ),
]
```

## Firing only in the absence of something

`AbsentConsume(T, absent_type=U, key=...)` fires for a `T` that has no matching
`U` — the declarative form of "don't do this twice":

```python
# not-run: illustrative
from reactifact.consume import AbsentConsume

# file a ticket for a thread that has none yet
consumes = [
    AbsentConsume(Escalation, absent_type=HelpdeskTicket, key=lambda d: d.thread_id),
]
```

Unlike `JoinConsume`, it only reacts to `T`'s own events (deleting a `U` does
not re-fire it).

## Both at once

`Read.correlated(...)`/`CorrelatedConsume` unifies the two: fire when every
`require` type is present for a key **and** no `forbid` type is:

```python
# not-run: illustrative
from reactifact import Read

Read.correlated(
    key=lambda d: d.thread_id,
    require=[Report, PendingQuestion],
    require_conditions={PendingQuestion: lambda d: d.answered},
    forbid=[HelpdeskTicket],
)
```

`key` receives an artifact's `.data`. `require_conditions`/`forbid_conditions`
filter candidates of one type (e.g. only an *answered* question completes the
group; only a *sent* notification blocks it).

## Typed-parameter sugar

Instead of spelling `Read` objects, annotate `run()`/`@step` parameters:
`Question`, `list[Question]`, `Question | None`, `Artifact[Question]`,
`Latest[Question]`, `Annotated[T, When(fn)]`, `Annotated[T, Wakes(False)]`,
`Annotated[T, Unscoped()]`. With an explicit `reads=`, typed params missing from
it are added as read-only inputs automatically. Use `Read` objects when the
event/required/condition semantics are not obvious from the annotation.
