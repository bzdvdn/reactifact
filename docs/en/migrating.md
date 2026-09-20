# Migrating from LangGraph / CrewAI / LlamaIndex / plain Python

You don't move everything at once. The recommended path is **port one piece,
keep it running next to the old code, and grow from there** — reactifact is a
library, it composes with whatever already works. This page is the *how*; the
[port matrix](port-matrix.md) is the *what maps to what*, and each pattern below
links to a runnable example.

If you only take one idea away:

> **State is primary, execution is derived.** Instead of drawing what runs next,
> you declare what artifacts exist and what each agent consumes/produces; the
> runtime decides what runs from the state changes.

## 1. Concept map

| You have (LangGraph / LangChain / CrewAI / LlamaIndex) | reactifact |
| --- | --- |
| node + `add_edge` / `add_conditional_edges` | a `Produce` + a `Consume(Type)` — the edge *is* "who consumes this type" |
| a shared `TypedDict`/dict state, mutated by nodes | typed, versioned `Artifact`s in the `Context` |
| reducer (`Annotated[list, add]`) | `self.effects.create/update/link(...)` compiled into one atomic `Patch` |
| checkpointer / thread state | `Context` (git-like), `Session`/`SessionStore`, `context.branch()`/`merge()` |
| `interrupt()` / `Command(resume=...)` | `effects.ask(...)` → `PendingQuestion`, answered with `effects.resume(...)` |
| subgraph / nested agent | `AgentAsTool` (isolated nested runtime) or `context.branch()` |
| conditional router / supervisor | `Consume.condition` / `Consume.by_field`, or `recipes.Router` |
| RAG chain (retriever → prompt → LLM) | `Source` + `fan_out_sources` + `materialize_doc` + produces (`quick.rag` for the simple case) |
| tool loop (`create_react_agent`) | `LLMAgent`/`ToolUse` (or `ToolUseHITL` for approvals), or `native_tool_use` / `recipes.run_tool_loop` |
| `MemorySaver` + summary node | `Msg` artifacts + `context.view` + `recipes.WindowSummarizer`/`RollingDigestSummarizer` |
| callbacks / LangSmith / Langfuse | native traces (`reactifact.tracing`) + `redactor=`, export to Langfuse/OTLP/Postgres |

## 2. Port one node (before → after)

A LangGraph node that classifies then answers:

```python
def classify(state):            # node
    return {"route": "billing" if "invoice" in state["text"] else "general"}

def answer(state):              # node
    return {"reply": llm(state["text"], route=state["route"])}

graph.add_edge(START, "classify")
graph.add_edge("classify", "answer")
```

On reactifact the *edges disappear* — they become `consume`/`produce`:

```python
from pydantic import BaseModel
from reactifact import Consume, Produce, ProduceCall, produce


class Question(BaseModel):
    text: str


class Route(BaseModel):
    thread_id: str
    kind: str


class Reply(BaseModel):
    text: str


@produce(Route)
async def classify(call: ProduceCall) -> None:
    question = call.trigger
    if question is None or not isinstance(question.data, Question):
        return None
    kind = "billing" if "invoice" in question.data.text.lower() else "general"
    call.effects.create(Route(thread_id=question.id, kind=kind), id=f"route:{question.id}")


@produce(Reply)
async def answer(call: ProduceCall) -> None:
    route = call.trigger  # this produce runs because a Route was created
    if route is None or not isinstance(route.data, Route):
        return None
    # ... call the model with run-scoped input ...
    call.effects.create(Reply(text="…"), id=f"reply:{route.data.thread_id}")
```

```python
from reactifact import Consume, Context, Runtime, create_agent

answerer = create_agent(
    "answerer",
    consumes=[Consume(Reply)],       # the "edge" from classify to answer
    produces=[answer],
)
```

Two things to notice, because they're the migration's real work:

- **Eligibility is a state decision, not a placement.** The old graph said
  "answer runs after classify". Here `answer` runs *because a `Route` exists* —
  so a guard (`return None` when the input isn't ready) is the sequencing
  mechanism, not an edge.
- **Stable ids.** `f"route:{question.id}"` makes re-runs idempotent (§42) — the
  same event twice never duplicates state. This replaces most hand-written
  "already done?" bookkeeping.

## 3. State dict → artifacts

```python
# before: one growing dict
state["facts"].append({"text": "...", "source": url})

# after: typed artifacts + provenance
fact = self.effects.create(Fact(text="...", source=url))
fact.link("extracted_from", doc)
```

Nothing is mutated in place: an update is a new version, so `context.diff(v1,
v2)` is a real operation and provenance (`supported_by`/`derived_from`/…) is the
same graph the runtime uses to decide what to re-run — not a logger bolted on.
See [`examples/ledger`](../../examples/ledger/README.md) for the recompute argument
made concrete.

## 4. Checkpointing, threads, time-travel

- Persist conversations with `SessionStore(FileKVBackend(...))` +
  `ChatAssistant` (`examples/support_copilot`, `examples/knowledge`).
- Fork an exploration with `context.branch()`, run different agents on each
  fork, and `merge()` them back — with a real `MergeConflict` when both forks
  touch the same artifact (`examples/forklab`).
- Reproduce a past state with `reactifact.replay` / `replay_context`; pin a run's
  fingerprint with `reactifact.audit.context_hash` (`examples/fintech_audit`).

## 5. Interrupts → human-in-the-loop

```python
# ask
self.effects.ask("Approve the refund?", kind="approve", id=f"approve:{qid}")
# resume (from your HTTP handler / CLI)
context.resume(question.id, "yes")
```

The human is just another reaction (`PendingQuestion` is an artifact); tools can
be gated the same way — see `examples/repo_agent` (a `@tool(destructive=True)`
that only runs after an approval) and `examples/devops`.

## 6. RAG chain → sources

```python
from reactifact.quick import rag

r = rag({"docs": "./docs", "costs": "./costs.csv"})
answer = await r.ask("what's the total gpu cost?")   # answer.text, answer.sources
```

For full control, compose the same pipeline by hand with `fan_out_sources` +
`materialize_doc` and produce an `Answer` linked `supported_by` the documents
(`examples/knowledge`, `examples/research`). Retrieval is a `Source` capability
(filesystem/CSV/embeddings/web), not a hardcoded chain — swap the source, keep
the agents.

## 7. Tool loops

- Model decides which tool, one step at a time: `LLMAgent` / `HITLLMAgent`
  (`examples/devops`).
- OpenAI-native `tools`/`tool_calls`: `reactifact.native_tool_use`
  (composable functions), or `recipes.run_tool_loop` for a bounded, parallel,
  mandatory-tool-aware loop.
- Budgets and honest failure come for free: `Budget(max_runs=…, max_tool_calls=…)`,
  and a produce returns `None` instead of a confident guess (§59).

## 8. Don't go all-or-nothing (interop)

- **Call reactifact from a node.** Your LangGraph node can build a `Context`, run
  a `Runtime`, and return the result — move one auditable/calculating step at a
  time.
- **Expose reactifact as MCP.** `create_mcp_server(tools, context=ctx)` publishes
  your `Tool`s (and read-only `context://artifacts/...` resources) so any MCP
  client — Claude, another agent framework — can call the reactifact part.
- **Coexist by concern.** Keep your orchestration where it works; use reactifact
  where provenance, determinism, and auditability matter most (the
  [fintech_audit](../../examples/fintech_audit/README.md) shape).

## 9. Ops mapping

| Concern | reactifact |
| --- | --- |
| tracing | `Tracer(store=TraceStore(...))`, `LangfuseTracer`, `OTLPTracer`, `PostgresStore`; scrub with `RuntimeResources(redactor=…)` |
| sessions | `SessionStore` over `FileKVBackend`/`SQLiteKVBackend`/`PostgreSQLKVBackend` |
| budgets / limits | `Budget(max_runs, max_seconds, max_iterations, max_tool_calls)` |
| error policy | fail-loud by default; `Runtime(isolate_errors=True, on_agent_error=…)` opts into partial progress |
| per-request data | `Runtime.arun(request={...})` → `call.request` (no ContextVar plumbing) |
| resource lifecycle | `RuntimeResources.scope(factory)` / `async with` (loop-safe clients) |

## 10. Migration checklist

- [ ] Every intermediate value is a **typed `Artifact`**, not a dict/`TypedDict`.
- [ ] Each unit is a `Produce` that declares `consumes`/`produces`; no node calls
      another directly.
- [ ] Eligibility is a guard (`return None`), not a scheduling order.
- [ ] Ids are **stable and re-derivable** (`f"answer:{qid}"`,
      `effects.create_once(...)`, `effects.create_once_from(...)`).
- [ ] Derived artifacts `link(...)` to their inputs (provenance).
- [ ] Model calls go through `structured_llm`/`llm_reply` and return `None` on
      failure — the caller shows an honest fallback, never a fake answer.
- [ ] A `Budget` is set for production runs.
- [ ] Human steps are `effects.ask`/`resume`, not a special case.
- [ ] Sessions use `SessionStore`; resources are built per loop
      (`RuntimeResources.scope`).
- [ ] Tracing is wired (`Tracer(store=...)`), and `redactor=` is set if data is
      sensitive.

## 11. Gotchas

- **There is no `add_edge`.** If you find yourself wanting one, the edge is a
  `Consume(ProducedType)` on the downstream agent.
- **`effects`, not `return patch`.** Write `self.effects.*` and return `None`; the
  runtime compiles the effect set into one atomic commit (`Produce` returning a
  `Patch` is the low-level escape hatch, not the idiom).
- **Artifacts aren't messages.** Model input is built from a `context.view(...)`
  / consumed artifacts, not a free-floating string list.
- **Determinism is a habit.** Use stable ids and keep timestamps/randomness out of
  what you hash — `context_hash` then makes runs comparable
  (`examples/fintech_audit`).

## 12. Where to look next

- [Port matrix](port-matrix.md) — canonical pattern → reactifact example.
- [Quickstart](quickstart.md) — the four `quick` cases in a few lines.
- [Scheduler semantics](scheduler-semantics.md) — the execution contract you're
  moving onto.
- [Patterns](patterns.md) / [Recipes](recipes.md) — reusable building blocks.
