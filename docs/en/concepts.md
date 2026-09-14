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

An `Artifact` is the pair `(id, data, created_at, …)` where `data` is a
`pydantic` model. Artifacts are **first-class objects**, not string blobs.

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

The authoring surface is **`self.effects`** (see
[The produce contract](effects.md), §24): a produce writes
`create/update/link/ask` and returns `None`. The runtime compiles the effect
set into one atomic **`Patch`** — its compiled transport.

```python
async def produce(self, context, inputs, event=None):
    answer = self.effects.create(Answer(query_id=qid, text=text), id="answer:q1")
    answer.link("supported_by", evidence_id)
    self.effects.update(some_artifact, status="answered")
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

An `Agent` is a **thin container**: it declares what it reacts to and what it
can produce. The logic lives in `Produce` classes.

```python
class RepairFlow(Agent):
    name = "repair_flow"
    consumes = [Consume(UserMsg), Consume(Project)]
    produces = [
        CollectStage(), PickStage(), PlanStage(),
        EstimateStage(), ApprovalStage(), AssistantStage(),
        Produce(ChatReply), Produce(PendingQuestion),
    ]
```

- `consumes` — artifact types that wake this agent.
- `produces` — `Produce` instances that may run when the agent is awake.
- The runtime wakes agents on events, respecting budget and concurrency.

`Agent` also has a lower-level `run(event, context) -> Patch | None` you can
override directly instead of declaring `produces`. It's an escape hatch for
assembling a `Patch` by hand (see [patterns.md](patterns.md) for when that's
actually warranted) — not a third everyday style alongside `Produce`
subclasses and `@produce` functions. Reach for it only when you've outgrown
both.

## 5. Produce

`Produce[M]` is where the work happens. Its **authoring surface is
`self.effects`** (§24): a produce writes what should change
(`effects.create/update/link/ask`) and returns `None`. The runtime compiles the
effect slot into one atomic patch — commit, events, trace, validation stay the
same, but the produce itself never builds a `Patch` (that type is now the
runtime's transport).

```python
class EstimateStage(Produce[Project]):
    artifact_type = Project

    async def produce(self, context, inputs, event=None) -> None:
        ...
        self.effects.update(project_art, stage="estimate")
        return None
```

`fan_out_sources` / `materialize_doc` (recipes) also write into the current
effect slot, and HITL is an `effects.ask(...)` (a `PendingQuestion` artifact,
§60).

Convention: **determinism first** (§67). Whether a stage is eligible is decided
by a guard — `if project.stage != "estimate": return None`. Whether it should
change is a pure function of state. LLM use is reserved for genuinely
generative tasks and is always wrapped with a structured schema and a fallback.

A `Produce` can also take dependencies it needs to run before it via the
`depends_on`/`inject` mechanism — see the [API reference](api.md).

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

- **`Budget` / `RunOutcome` / `RunStats`** — limit runs, iterations, and time;
  on budget decline the runtime stops and reports the reason.
- **`Event` / `EventType`** — the wire format of "something changed"; agents are
  woken by artifacts' create/update events, and the announce mechanism emits
  `status` events on the way to the UI. `ARTIFACT_STALE` fires automatically
  when an artifact's recorded dependency (§43-44) gets a newer version — opt
  in with `Consume(Type, event_types=[EventType.ARTIFACT_STALE])`.
- **`Trigger`** — secondary enter conditions for a produce (e.g. a periodic or
  timer-based wake), independent of the artifact consums.
- **`Session` / `SessionStore`** — durable, per-chat working memory across
  requests, backed by a KV checkpoint (file or SQLite).