# Migrating from reactifact 0.1.x to 0.20.0

This guide is for applications that already use reactifact. If you are porting
from LangGraph, CrewAI, LlamaIndex, or plain Python, start with
[Migrating from other frameworks](migrating.md) instead.

0.20.0 is a breaking release, but the migration is mechanical for most apps:
the runtime and the commit DAG are the same, while the authoring surface gains
explicit types. The old names remain importable through the 0.20 compatibility
window; this page shows the replacements and what to plan for 0.21.0.

## 1. What changed

| 0.1.x | 0.20.0 |
| --- | --- |
| `Produce` / `@produce` / `ProduceCall` | `Step` / `@step` / `StepCall` |
| `Agent.produces=[...]` | `Agent.steps=[...]` |
| contracts split between `Agent.consumes` and `Produce.reacts_to` | each step owns `consumes` / `produces` |
| `call.trigger` + manual `isinstance` guards | typed `T` / `Artifact[T]` inputs, or `Consume(T)` |
| manual `context.get(stage_id(...))` | `scope=` / `Scope(...)` + typed inputs, or `Latest[T]` |
| `self.effects.*` | `effects: Effects` in a class `run()`, or `call.effects` in a function step |
| mutable `Artifact` with `artifact.update()` | frozen handle; updates go through `Context` / `Effects` |
| `artifact.history` / `.at()` / `.diff()` | `Context.version_history` / `artifact_version` / version diffs |
| v1 session payload | v2 `blobs` + `data_ref`; v1 payloads migrate on load |
| `reactifact.tool_use`, `reactifact.native_tool_use`, `reactifact.agent_tool` | `reactifact.tools.use`, `reactifact.tools.native`, `reactifact.tools.agent` |

Everything marked as a bridge remains available in 0.20.x. Removal is planned
for 0.21.0, so finish the migration before that release.

## 2. Steps and agents

The class form keeps the same shape — declarations move onto the step and the
body receives typed inputs:

```python
from reactifact import Artifact, Effects, Produce, Step


class Estimate(Produce):
    artifact_type = Project
    reacts_to = Project

    async def produce(self, call: ProduceCall) -> None:
        self.effects.update(call.trigger, stage="estimate")
```

becomes:

```python
from reactifact import Artifact, Effects, Step


class Estimate(Step):
    consumes = (Project,)
    produces = (Project,)

    async def run(self, project: Artifact[Project], effects: Effects) -> None:
        effects.update(project, stage="estimate")
```

The agent lists steps instead of produces:

```python
agent = Agent(produces=[Estimate()])
```

becomes:

```python
agent = Agent(steps=[Estimate()])
```

`Agent.produces` remains a compatibility property. New code should use
`Agent.steps`; read `agent.steps` when introspecting an agent.

The function form changes `@produce` to `@step`, and `call.effects` replaces
`self.effects`:

```python
from reactifact import Answer, Evidence, step


@step(produces=Answer, consumes=Evidence)
async def answer(call):
    return Answer(text=call.trigger.data.text)
```

Function steps may also take typed inputs:

```python
@step(produces=Answer, consumes=Evidence)
async def answer(evidence: Evidence, effects: Effects) -> Answer:
    return Answer(text=evidence.text)
```

## 3. Typed inputs and correlation

In 0.1.x a step opened with guards:

```python
if call.event is None or not isinstance(call.trigger.data, Question):
    return None
question = call.trigger.data
```

In 0.20.0 the input contract is the type annotation:

```python
class Research(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, question: Question, effects: Effects) -> Answer:
        return Answer(text=question.text)
```

Use `Artifact[T]` instead of `T` when the step needs the handle, id, or
provenance. Use `list[Artifact[T]]` for every matching version of a type.

Correlation that used to be a stable-id lookup is now a `Scope`:

```python
class BuildAnswer(Step):
    consumes = (
        Consume(ResearchTurn),
        Consume(Evidence, wakes=False),
    )
    produces = (Answer,)
    scope = "query_id"

    async def run(
        self,
        turn: ResearchTurn,
        evidences: list[Artifact[Evidence]],
        effects: Effects,
    ) -> None:
        ...
```

`scope=` keeps artifacts with the same `query_id` together. `Consume(...,
wakes=False)` is the replacement for reading an input without re-running on
every update. `Latest[T]` selects the newest value when a step should not see
the whole history. Use `When(...)` for value conditions and `Wakes(False)` for
non-waking inputs.

## 4. Effects

`self.effects` is gone. A class step receives `effects: Effects`; a function
step uses `call.effects`:

| 0.1.x | 0.20.0 |
| --- | --- |
| `self.effects.create(value)` | `effects.create(value)` |
| `self.effects.update(artifact, **fields)` | `effects.update(artifact, **fields)` |
| `self.effects.link(source, "relation", target)` | `effects.link(source, "relation", target)` |
| `self.effects.announce(...)` | `effects.announce(...)` |

Returning a `BaseModel` from `run()` is create-sugar. Anything beyond creating
the declared output — updates, links, extra creates, HITL — goes through
`effects`. The declarations still validate the commit: every created type must
be listed in `produces`; put the primary output first, then any extra creates:
`produces=(Receipt, PendingAction)` (the old `also_creates=` spelling is a
deprecated alias).

## 5. Artifacts and version history

`Artifact` is now a frozen handle into a content-addressed version store.
Do not mutate it and do not call `artifact.update()`.

| 0.1.x | 0.20.0 |
| --- | --- |
| `artifact.update(data)` | `ctx.update(artifact.id, data)` (returns a new handle) |
| `artifact.history` | `ctx.version_history(artifact.id)` |
| `artifact.at(version)` | `ctx.artifact_version(artifact.id, version)` |
| `artifact.diff(other)` | `ctx.version_diff(...)` / `ctx.diff(...)` |

The old `Artifact.at/history/diff` methods remain as deprecated bridges in
0.20.x. Compaction is authoritative: `ctx.compact(keep_versions=...)` selects
which versions remain readable, and deleted artifacts keep readable history.
Code that relied on a mutable `_history` list should read versions through
`Context`.

## 6. Sessions and persistence

Session payloads are v2: an artifact version index plus `blobs` keyed by
`(data_type, content_hash)`, with commit `Create`/`Update` operations using
`data_ref`.

No code change is needed for existing stores: `Context.from_dict` still loads
v1 payloads, and the next save writes v2. If you inspect payloads directly,
read versions through the public `Context` methods rather than depending on the
old `data`/`history` layout.

## 7. Tool imports

The old top-level modules are shims. Prefer the package paths:

```python
from reactifact.tools import tool
from reactifact.tools.use import ToolUse, ToolUseHITL
from reactifact.tools.native import parse_tool_calls
from reactifact.tools.agent import AgentAsTool
```

| 0.1.x | 0.20.0 |
| --- | --- |
| `reactifact.tool_use` | `reactifact.tools.use` |
| `reactifact.native_tool_use` | `reactifact.tools.native` |
| `reactifact.agent_tool` | `reactifact.tools.agent` |

The shims stay through 0.20.x and are scheduled for removal in 0.21.0.

## 8. Removed examples

The 0.1.x tree contained many small examples that duplicated the same idea.
0.20.0 keeps ten reference applications: `devops`, `knowledge`, `forklab`,
`repair`, `medic_lab`, `fintech_audit`, `starter_app`, `chat`, `outbox`, and
`repo_agent`; `tour` isolates the primitives. If an old
example was your starting point, use the closest survivor and the
[patterns](patterns.md) page. The deleted examples are not part of the public
API.

## 9. Migration checklist

1. Replace `Produce` with `Step`, `ProduceCall` with `StepCall`, and
   `@produce` with `@step`.
2. Replace `Agent(produces=[...])` with `Agent(steps=[...])`; move
   `Agent.consumes` contracts onto the relevant steps.
3. Replace `self.effects.*` with the injected `effects` argument, or
   `call.effects` in function steps.
4. Replace `call.trigger.data` guards with typed parameters, `Consume(...)`,
   `Latest[...]`, or `When(...)`.
5. Replace manual stable-id correlation with `scope=` / `Scope(...)`.
6. Replace `artifact.update()` with `ctx.update()`; move history reads to
   `Context.version_history` / `artifact_version`.
7. Update tool imports to `reactifact.tools.*`.
8. Search for leftovers:

   ```bash
   rg "self\.effects|Agent\(produces=|\.update\(.*artifact|artifact\.history|reactifact\.tool_use|reactifact\.native_tool_use|reactifact\.agent_tool" .
   ```

9. Run the test suite and fix `DeprecationWarning`s for `@produce` and the
   history bridges. Tests should pass without warnings from your own code.
10. If you persist sessions outside `SessionStore`, verify a stored v1 payload
    still loads and is rewritten as v2 on save.

## 10. Compatibility and removal

Kept in 0.20.x, removed in 0.21.0:

- `Produce`, `ProduceCall`, `@produce`
- `Agent.produces`
- `Artifact.at`, `Artifact.history`, `Artifact.diff`
- `reactifact.tool_use`, `reactifact.native_tool_use`, `reactifact.agent_tool`

`@produce` emits a `DeprecationWarning`; the aliases and bridges do not. Treat
all of them as migration aids, not as a second API.

If something in your app has no direct mapping, check the
[0.20.0 changelog](https://github.com/bzdvdn/reactifact/blob/master/CHANGELOG.md) and the [patterns](patterns.md) page
before opening an issue.
