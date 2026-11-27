# Consume reference

A `Consume` answers two questions: *does this agent run at all* (`wakes`) and
*which artifacts become its inputs* (`collect`). All parameters can be class
attributes (for a `StepGroup` subclass) or constructor arguments.

## Common parameters

| Parameter | Meaning |
| --- | --- |
| `artifact_type` | the artifact type this consume watches |
| `condition` | `Callable[[Artifact], bool]` — extra filter (gets the wrapper) |
| `event_types` | which events wake it (default `CREATED`, `UPDATED`) |
| `wakes` | `False` = feed inputs but never trigger a run (default `True`) |
| `debounce` | collapse several same-generation events into one run |

`Consume(T)` fires when a `T` is created or updated.

## Conditional consumes

```python
# not-run: illustrative
from reactifact.consume import Consume

Consume(Question, condition=lambda a: len(a.data.text) > 0)
Consume.by_status(Report, "approved")          # Report.status == "approved"
Consume.by_field(Ticket, "severity", "high")   # Ticket.severity == "high"
```

`by_status` is just sugar for a `status`-field condition; `by_field` for any
field. Both accept `wakes=`/`debounce=`.

## Lifecycle events

`Consume(T)` covers create + update. Narrow it with a lifecycle subclass:

```python
# not-run: illustrative
from reactifact.consume import OnCreate, OnDelete, OnStale, OnUpdate

OnCreate(T)   # only a new id
OnUpdate(T)   # only an existing id / changed data
OnDelete(T)   # deleted; use event/Trigger, the data is gone
OnStale(T)    # a recorded dependency got a newer version
```

The lowercase factory was removed; instantiate `Consume` or a lifecycle class directly.

## Scope and history

A step `scope` (class attribute or `@step(scope=...)`/`Step(..., scope=...)`)
filters typed inputs to artifacts carrying the trigger's correlation value.
`Annotated[T, Unscoped()]` opts one parameter out of that filter, which is how a
step reads history across turns:

```python
# not-run: illustrative
from typing import Annotated
from reactifact import Artifact
from reactifact.step import Latest, Unscoped, Wakes

async def run(
    self,
    question: Question,
    answers: Annotated[list[Artifact[Answer]], Unscoped()],
    digest: Annotated[Latest[Digest], Unscoped()],
    # read history without waking on every Answer:
    # Annotated[list[Artifact[Answer]], Unscoped(), Wakes(False)]
) -> None: ...
```

Use `list[T]` for data, `list[Artifact[T]]` for handles.

## Reading without waking

`wakes=False` is the correct way to give an agent context it should *see* but
not *run on*:

```python
# not-run: illustrative
consumes = [
    Consume(Evidence),               # wake: run when evidence exists
    Consume(Question, wakes=False),  # input only: don't run per question
]
```

## Joining several artifacts

`JoinConsume(A, B, key=...)` fires once artifacts of every listed type exist
sharing a correlation key, and feeds all of them as inputs:

```python
# not-run: illustrative
from reactifact.consume import JoinConsume

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

`CorrelatedConsume` unifies the two: fire when every `require` type is present
for a key **and** no `forbid` type is:

```python
# not-run: illustrative
from reactifact.consume import CorrelatedConsume

CorrelatedConsume(
    key=lambda d: d.thread_id,
    require=[Report, PendingQuestion],
    require_conditions={PendingQuestion: lambda d: d.answered},
    forbid=[HelpdeskTicket],
)
```

`key` receives an artifact's `.data`. `require_conditions`/`forbid_conditions`
filter candidates of one type (e.g. only an *answered* question completes the
group; only a *sent* notification blocks it).

## `debounce`

When a fan-out creates N artifacts of the same type in one commit, N events
would wake the agent N times. `debounce=True` collapses them into a single run.
The run still sees all of them via `context.list_artifacts(T)` — do not rely on
`call.trigger` for "what changed" in a debounced step.
