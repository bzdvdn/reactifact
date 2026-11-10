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
| `diff / rollback / merge` | inspect or unwind history; merge forks |

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
[The produce contract](effects.md), §24): a produce writes
`create/update/link/ask` and returns `None`. A class-style step declares
`effects: Effects` in `run()`; a `@step` function uses
`call.effects`. The runtime compiles the effect set into one atomic
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

HITL is an `effects.ask(...)` → `PendingQuestion`, answered via
`effects.resume(...)` (see [patterns](patterns.md)).

## 4. Agent

An `Agent` is a **thin container**: it groups `Step`s. Each step declares what
it consumes and produces; the runtime derives the agent's triggers from those
contracts.

```python
class RepairFlow(Agent):
    name = "repair_flow"
    steps = [
        CollectStage(), PickStage(), PlanStage(),
        EstimateStage(), ApprovalStage(), AssistantStage(),
    ]
```

- `steps` — the unit-of-work instances. A step's `consumes` both feeds its
  typed inputs and decides which events can run it; `produces` is the allowed
  create set. Put the primary output first and extra create-only types in the
  same tuple: `produces = (Primary, Extra)`; `also_creates` is a deprecated
  alias.
- `Agent.consumes` still exists for the imperative escape hatch (an `Agent`
  subclass overriding `run(event, context)` directly), but declarative agents
  should put contracts on steps.
- `Consume(..., wakes=False)` reads an input without waking on it;
  `debounce=True` collapses several same-generation events into one run;
  `Consume.by_base(Base)` also wakes on subclasses. `JoinConsume`/
  `AbsentConsume` correlate across types — see [patterns](patterns.md).
- The runtime wakes agents on events, respecting budget and concurrency.

`Agent.run(event, context) -> Patch | None` remains the low-level escape hatch
for hand-assembling a `Patch`; it is not a third everyday style.

## 5. Step

`Step` is where the work happens. A class step declares `consumes`/`produces`
and receives typed inputs plus `effects: Effects` in `run()`:

```python
from reactifact import Artifact, Effects, Step

class EstimateStage(Step):
    consumes = (Project,)
    produces = (Project,)

    async def run(self, project_art: Artifact[Project], effects: Effects) -> None:
        ...
        effects.update(project_art, stage="estimate")
```

The function form takes one `call: StepCall` and uses `call.effects`:

```python
@step(produces=Answer, consumes=Evidence)
async def answer(call: StepCall) -> Answer:
    return Answer(text=call.trigger.data.text)
```

Returning a model is create-sugar; `effects.*` is for updates, links, HITL and
extra creates. `produces` validates every create type, including effect-created
ones. See [effects](effects.md) and [patterns](patterns.md).

Convention: **determinism first** (§67). Eligibility is decided by the step's
`consumes`/`When` selectors, not by a manual graph edge; LLM use is reserved for
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
  in with `Consume(Type, event_types=[EventType.ARTIFACT_STALE])`.
- **`Trigger`** — secondary enter conditions for a produce (e.g. a periodic or
  timer-based wake), independent of the artifact consums. `context_condition`
  is the two-artifact form (needed for joins/correlation); `debounce` is the
  hint `Runtime` reads to collapse repeat events into one run.
- **`Session` / `SessionStore`** — durable, per-chat working memory across
  requests, backed by a KV checkpoint (file or SQLite); `Context.compact(...)`
  bounds a long session's history.