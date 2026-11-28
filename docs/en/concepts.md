# Concepts

The full design rationale lives in
[constitution.md](../constitution.md). This page is the operative overview:
the six building blocks and how they interact.

## 1. Context

`Context` is the versioned working state. Like git, it keeps a history of
commits, each commit being the result of applying one or more **patches**.

```python
from reactifact import Context, RuntimeResources
from reactifact.sources import FileSystemSource

ctx = Context(
    resources=RuntimeResources(
        sources={"docs": FileSystemSource("./docs")},
    )
)
```

Key capabilities:

| Capability | Purpose |
| --- | --- |
| `create / update / delete` | encode intent (delegated per-run to agents) |
| `list_artifacts(Model)` | query the current state by artifact type |
| `view((M1, M2), condition=…)` | query a type join for decisions/chat memory |
| `related(artifact_id, relation)` | walk the provenance graph |
| `announce(message, kind=…)` | emit a progress/status event to the UI |
| `diff / checkout / merge` | inspect or unwind history; merge forks |

`Context.resources` carries what is *not* state: providers (the LLM, an
embedder), sources, and arbitrary app resources (a price catalog, an image
directory). Agents read them; they never persist them.

## 2. Artifact

An `Artifact` is an immutable handle `(id, version, data, content_hash, …)`
where `data` is a `pydantic` model. Artifacts are **first-class objects**, not
string blobs. Every revision lives in the context's `ArtifactStore`; reads via
`Context.artifact_version`/`version_history`/`find_versions`, while
`Artifact.at/history` are compatibility bridges. Mutation is `Context.update`
(or effects), never mutating `artifact.data` in place — the content hash is the
identity.

```python
class Evidence(BaseModel):
    query_id: str
    source: str
    text: str
    score: float
```

Rules of thumb:

- Every artifact has a stable `id`. Prefer stable ids over random ones
  (`answer:{query_id}`, `ref:{stable_id}:{owner}`) — idempotency and
  provenance linking become trivial.
- `query_id` is the conventional owner key when many artifacts belong to one
  workflow turn (a question, a research turn). Recipes and examples rely on it.
- Artifacts are immutable as *data*; change is expressed through patches that
  create new versions.

## 3. Effects & Patch

The authoring surface is the **effects** slot (see
[The step contract](effects.md), §24): a step writes
`create/update/delete/link/upsert` and returns `None`. A class-style step declares
`effects: Effects` in `run()`; a `@step` function uses
`inv.effects`. The runtime compiles the effect set into one atomic
**`Patch`** — its compiled transport.

```python
from reactifact import Effects

async def run(self, effects: Effects) -> None:
    answer = effects.create(Answer(query_id=qid, text=text), id="answer:q1")
    answer.link("supported_by", evidence_id)
    effects.update(some_artifact, status="answered")
    return None
```

The **compiled operations** (`reactifact.operations`):

| Op | Meaning |
| --- | --- |
| `Create` | add an artifact (optionally with a stable `id`) |
| `Update` / `update_fields` | a new revision of an artifact |
| `Delete` | remove an artifact |
| `Link` | connect `source →rel→ target` (provenance) |
| `Unlink` | remove a relation |

`Patch` (the container) is built by the runtime and by the `Agent.run` escape
hatch; effects compose *within* one produce, so nothing is applied until the
runtime compiles — atomicity is structural (§41).

HITL is an `human.ask(...)` → `PendingQuestion`, answered via
`human.resume(...)` (see [patterns](patterns.md)).

## 4. Agent (optional container)

Steps are the model. **`Runtime(ctx, steps=[...])`** wraps each step in an
implicit agent of one, so the common case needs no container at all. A
**`Agent`** is the optional container for steps that must share something
operational: one trigger, retry policy, timeout, priority, concurrency limit, or
one atomic commit. `Runtime` accepts `steps=` and `agents=` together (steps first).

```python
from reactifact import Agent

RepairFlow = Agent(
    "repair_flow",
    steps=[
        CollectStage(), PickStage(), PlanStage(),
        EstimateStage(), ApprovalStage(), AssistantStage(),
    ],
)
```

- `steps` — the unit-of-work instances. A step's `reads` feeds its
  typed inputs and gates it until those artifacts exist; `produces` is the allowed
  create set. Put the primary output first and extra create-only types in the
  same tuple: `produces = (Primary, Extra)`; the old `also_creates=` spelling
  was removed in 0.23, so extra types belong in `produces` itself.
- `Agent.consumes` still exists for the imperative escape hatch (a group
  subclass overriding `run(event, context)` directly), but declarative code
  should put contracts on steps.
- When `consumes` is explicit, typed artifact params missing from it are added
  as read-only inputs automatically; `Consume(..., wakes=False)` remains for an
  explicit list without a typed param. `IdempotentConsume(T, id_of=...)` wakes
  only while the derived target id is absent. `debounce=True` collapses several
  same-generation events into one run; `Consume.by_base(Base)` also wakes on
  subclasses. `JoinConsume`/`AbsentConsume` correlate across types — see
  [patterns](patterns.md).
- The runtime wakes groups on events, respecting budget and concurrency.

`Agent.run(event, context) -> Patch | None` remains the low-level escape
hatch for hand-assembling a `Patch`; it is not a third everyday style.
The 0.x `Agent`/`create_agent` aliases are removed in 0.24; use `Agent(...)`.

## 5. Step

`Step` is where the work happens. A class step declares `reads`/`produces`
and receives typed inputs plus `effects: Effects` in `run()`:

```python
from reactifact import Artifact, Effects, Progress, Step

class EstimateStage(Step):
    reads = (Project,)
    produces = (Project,)

    async def run(
        self,
        project_art: Artifact[Project],
        effects: Effects,
        progress: Progress,
    ) -> None:
        ...
        progress("Считаю смету…")
        effects.update(project_art, stage="estimate")
```

`progress` is optional: it publishes an ephemeral UI status (`ProgressEvent`)
without injecting the whole `Context`. Use it when a step only announces and
then works with an LLM, tools or domain logic. The same resolver also injects
other non-model parameters from `RuntimeResources`, including optional ones:
`llm: LLMProvider | None` gives the configured provider or an honest `None`.
An explicitly registered type wins over artifact resolution, so a pydantic
settings object (`resources.register(Settings, Settings(...))`) can be injected
as a resource; unregistered pydantic models stay artifacts.

A step's typed signature is its **read set**; an explicit `reads=` narrows it. A typed artifact parameter that is not in `reads` is
added automatically as a read-only input (`Read(T, required=False)`):

```python
class PickStage(Step):
    reads = (UserMsg,)          # only a new message satisfies it

    async def run(
        self,
        user: Artifact[UserMsg],
        project: Artifact[Project],  # read-only: collected, never wakes
    ) -> Any:
        ...
```

`project` is still collected, validated and recorded as an input — it just never
wakes the step on its own. If `reads` is omitted, the typed signature derives
its reads; mark an individual read-only input there with
`Annotated[T, Wakes(False)]` or use `Read(T, required=False)`. `Latest[T]` remains an ad-hoc context query, not a
declared input.

`T` injects the data model, while `Artifact[T]` injects the handle (id, version,
for `Edit`/links). Add `| None` to either form when the input may be absent:
`Artifact[T] | None` gives the handle or `None`, not the data model.

The function form takes the same typed parameters:

```python
@step(produces=Answer, reads=Evidence)
def answer(evidence: Evidence) -> Answer:
    return Answer(text=evidence.text)
```

A step that needs the trigger id, the raw event, the per-turn request or the
effect slot takes one `inv: StepInvocation` instead — the low-level form.

Returning a model is create-sugar; `effects.*` is for updates, links, HITL and
extra creates. `produces` validates every create type, including effect-created
ones. See [effects](effects.md) and [patterns](patterns.md).

Convention: **determinism first** (§67). Eligibility is decided by the step's
`reads`/`When` selectors, not by a manual graph edge; LLM use is reserved for
genuinely generative tasks and is wrapped with a schema + fallback.

## 6. Provenance

Every derived artifact links to what produced it. The runtime records
**reads and writes** automatically; your code adds domain relations via
`patch.link`:

```text
Answer ──supported_by──► Claim ──derived_from──► Evidence ──extracted_from──► Doc
```

Why this matters:

- **Explainability** — "show your sources" is a query over relations, not LLM
  memory.
- **Scoring** — an answer's strength is the conjunction of its claims'
  confidence and their evidence support.
- **Deterministic auditing** — every run trace includes the reads/writes of each
  agent span.

## Supporting pieces

- **`Budget` / `RunOutcome` / `RunStats`** — limit runs, iterations, wall-clock,
  tool calls, and LLM tokens/cost; on exhaustion the runtime stops and reports
  the outcome (`RunStats` carries the token/cost totals; cost needs an injected
  `pricer`).
- **`Event` / `EventType`** — the wire format of "something changed"; agents are
  woken by artifacts' create/update events, and the announce mechanism emits
  `status` events on the way to the UI. `ARTIFACT_STALE` fires automatically
  when an artifact's recorded dependency (§43-44) gets a newer version — opt
  in with `Consume.on_stale(Type)` (or the explicit `Consume(Type, event_types=[...])`).
- **`Trigger`** — secondary enter conditions for a produce (e.g. a periodic or
  timer-based wake), independent of the artifact reads. `context_condition`
  is the two-artifact form (needed for joins/correlation); `debounce` is the
  hint `Runtime` reads to collapse repeat events into one run.
- **`Session` / `SessionStore`** — durable, per-chat working memory across
  requests, backed by a KV checkpoint (file or SQLite); `Context.compact(...)`
  bounds a long session's history.

## Which API do I use?

This is a ladder, not more concepts: start at the first row and only descend
when the task actually needs it.

### Authoring a step

| Need | Use | Notes |
| --- | --- | --- |
| One typed transformation | `@step(produces=..., reads=...)` | return the produced model, or use `inv.effects` for updates/links |
| Typed inputs, several effects, HITL | class `Step` with `run(..., effects: Effects)` | the full form; see [Effects](effects.md) |
| Hand-assembled operations / imperative agent | `Patch`, `Agent.run(event, context)` | escape hatch, not the everyday style |

### Scheduling

| Need | Use |
| --- | --- |
| React to a type | `Consume(Type)` |
| Only one lifecycle event | `Consume.on_create(Type)`, `Consume.on_update(Type)`, `Consume.on_delete(Type)`, `Consume.on_stale(Type)` |
| Conditional edge | `Consume.by_status(...)` / `Consume.by_field(...)`, `Annotated[T, When(...)]` |
| Read without waking / latest / scoped lookup | a typed param missing from explicit `reads` (automatic), `Annotated[T, Wakes(False)]` when `reads` is derived, `Consume(..., wakes=False)`, `Latest[T]`, `Scope(...)`, `scope=` on `Step`/`@step` |
| Cross-turn history | `Annotated[list[Artifact[T]], Unscoped()]` / `Annotated[Latest[T], Unscoped()]` (add `Wakes(False)` for reads) |
| Periodic/join/absent/debounced | `Trigger`, `JoinConsume`, `AbsentConsume`, `debounce=True` |

### Streams

| Need | Use |
| --- | --- |
| Canonical chat UI | `ChatEvent` from `Chat.stream()` / `create_chat_router` |
| Live UI / Studio / token deltas | `RunEvent` from `Runtime.astream_events(stream_mode=...)` |
| Simple CLI/status | `ProgressEvent` from `Runtime.astream()` (legacy status stream) |

### Persistence and memory

| Need | Use |
| --- | --- |
| Continue a conversation | `SessionStore` + `session_id` |
| Bound/summarize the prompt | `ChatMemory` (`WindowPruner` / `RollingDigestSummarizer`) |
| Remember facts across sessions | `MemoryStore` (`recipes`) |
| Inspect/rewind a turn | `ContextCheckpoint` (`context.checkpoints()` / `at()`) |
| Try alternative paths | `context.branch()` + `merge()` |

### Resource ownership

`RuntimeResources.aclose()` closes `llm` and `embedder`, plus every typed
resource registered with `owned=True` — in reverse registration order, before
the providers. Owned clients are duck-typed: an async `aclose()` or a sync
`close()`.

```python
resources.register(
    ImageRenderSettings,
    ImageRenderSettings(provider=image_from_env(), out_dir=...),
    owned=True,
)
...
await resources.aclose()  # owned resources, then embedder, then llm
```

Everything else (`sources`, shared clients, `MemoryStore`, DB pools) is
app-owned: leave `owned=False` (the default) and close it in your lifespan, not
per turn. The distinction matters for a callable `resources=` factory:
`ChatAssistant` closes that per-turn instance after every turn, so mark only
what the factory itself created.
