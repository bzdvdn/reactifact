<p align="center">
  <img src="docs/img/reactifact-hero.png" alt="reactifact — typed artifacts, reactive steps, auditable state" width="800">
</p>

**Agents that can prove their answers.** reactifact is a Python runtime for
typed, reactive agents: you declare artifacts and steps, the runtime derives the
path — no graph to wire. Contracts, scenarios, evals, traces and replay are
built in, local-first, with three core dependencies and no API key.

> **Your types are the graph. Your runs are the receipt.**

[![CI](https://github.com/bzdvdn/reactifact/actions/workflows/ci.yml/badge.svg)](https://github.com/bzdvdn/reactifact/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/bzdvdn/reactifact/graph/badge.svg)](https://codecov.io/gh/bzdvdn/reactifact)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://github.com/bzdvdn/reactifact)
[![PyPI version](https://img.shields.io/badge/pypi-reactifact-blue)](https://pypi.org/project/reactifact/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Ask DeepWiki](docs/img/deepwiki.svg)](https://deepwiki.com/bzdvdn/reactifact)
[![Docs](https://img.shields.io/badge/docs-bzdvdn.github.io%2Freactifact-blue)](https://bzdvdn.github.io/reactifact/)

> **Is:** a Python library (3 core deps) · single-process · typed, versioned artifacts with provenance · deterministic replay · scenarios + evals + traces in the box · optional code-first knowledge base · runs offline, no API key
> **Isn't:** a managed platform · a distributed task queue · a pre-built agent/tool marketplace

## The three things that matter

- **No graph to draw.** `@step(consumes=Question) -> Answer` is the route. The
  runtime derives execution from which artifacts exist — fan-out, joins,
  retries and loops are state changes, not edges you maintain.
- **Proof, not logs.** Every derived artifact links to its inputs, every answer
  carries the context hash, and a finished run replays deterministically.
  “Why did it say that?” is a query, not a guess.
- **The whole loop is built in.** Contracts, scenarios, dataset evals, traces,
  replay and branching live in the same runtime — no second stack for testing or
  observability.

## Sixty seconds, no API key

```bash
uvx reactifact demo        # or: uv add reactifact && uv run reactifact demo
uvx reactifact demo --web  # browser: answer, provenance, contracts, traces
```

```text
reactifact demo — computed, cited, reproducible

question What is the Q2 cloud spend variance, and does policy require approval?
sources  transactions.csv (6 rows) · budget.csv (2 rows) · policy.md

answer   Q2 cloud spend was $45,000 against a $40,000 budget (+12.5%) — exceeds
         the 10% policy threshold. CFO approval is required before the quarter
         is closed.

computed cloud spend $45,000 (2026-04 $12,000, 2026-05 $15,000, 2026-06 $18,000)
policy   threshold 10% → approval required
citations budget.csv, transactions.csv, policy.md

provenance (context.related(answer.id, "supported_by"))
  Answer    → supported_by → Variance, policy.md
  Variance  → calculated_from → Spend, budget.csv
  Spend     → calculated_from → transactions.csv

context sha256 ffeca1bb18cbc34749d3b5bdb40d85677b1ac33d47fb1160f6c89053694b2783
re-run         identical ✓
```

Three `@step` functions, none of which imports another by name: they declare
what they consume and produce, and the runtime derives the order from state.
The figures come from plain Python, and both the answer and the numbers carry
the hash of the exact state that produced them. The whole demo lives in
[`reactifact/demo.py`](reactifact/demo.py).

`demo --web` serves the same run at `/` with the provenance graph, plus
`/contracts`, `/traces`, `/api/demo` and `/docs`. No API key, in-memory traces.

## One pipeline, four surfaces

The same typed pipeline runs as a library, CLI, HTTP service, daemon or MCP
tool. Web and MCP are adapters, not the core.

```python
pipeline = Pipeline(steps=[...], seed_type=Question, answer_type=Answer, traces=True)

pipeline.run(seed)                         # library
app = pipeline.http()                      # /run, /stream, /docs, /contracts, /traces
```

| Surface | One line |
| --- | --- |
| **CLI** | `reactifact run app:pipeline "hello"` |
| **HTTP** | `app = pipeline.http()` — typed `/run`, SSE `/stream`, OpenAPI `/docs` |
| **Daemon** | `Daemon(pipeline, seed_source=next_tick, interval=60).serve()` |
| **MCP** | `create_mcp_server([pipeline.as_tool()])` |

Need sessions and history? `Chat(..., store="sessions.db").http()` adds
`/chat/stream` plus `/runs/{id}`; pass `ChatRoutes(invoke="/chat",
history="/chat/history", clear="/chat/clear")` for the DEVOPS-style paths.

## Define a step, run it

```python
from pydantic import BaseModel
from reactifact import Pipeline, step

class Question(BaseModel):
    text: str

class Answer(BaseModel):
    text: str

@step(consumes=Question)                 # produces=Answer inferred from ->
def answer(question: Question) -> Answer:
    return Answer(text=f"you asked: {question.text}")

pipeline = Pipeline(steps=[answer], seed_type=Question, answer_type=Answer)
result = pipeline.run(Question(text="why did cloud spend jump in Q2?"))

print(result.latest(Answer).data.text)
print(result.sources(result.latest(Answer)))   # citations, as a query
print(result.hash)                              # reproducible fingerprint
```

`Pipeline` owns steps, resources and defaults and builds a fresh `Context` +
`Runtime` per run. The CLI is the same pipeline:

```bash
reactifact run app:pipeline "why did cloud spend jump in Q2?"
```

`run` writes `.reactifact/traces.db` by default; `reactifact trace` renders the
latest run. Use `--no-trace` to skip it.

## The loop is built in

Start with a contract, test the agent, gate a dataset, then inspect the run that
actually happened:

| Stage | Built in |
| --- | --- |
| **Design** | `pipeline.validate()` / `.describe()`, contracts page, `reactifact validate` |
| **Test** | `ScenarioLab`, `@scenario`, tool faults, record/replay, `check --scenarios` |
| **Evaluate** | `Dataset` + deterministic metrics + LLM judge, `check --evals`, online eval |
| **Observe** | typed `TraceStore`, spans, reads/writes, provenance, `/traces` dashboard |
| **Debug** | `reactifact trace`, `replay`, `branch`, `diff`, context hashes |

One CI command:

```bash
reactifact check app.pipeline:pipeline \
  --scenarios app.scenarios \
  --evals evals/qa.json \
  --overall 0.8
```

It runs contracts, scenarios and dataset evals in one gate and exits non-zero
when any stage fails. The same loop is available as code (`pipeline.validate()`,
`ScenarioLab`, `evaluate`) — the CLI is not a separate implementation.

## What makes it different

| Traditional agent (LangGraph / CrewAI / LangChain) | reactifact |
| --- | --- |
| A program follows a graph / plan | Agents **react** to state changes |
| Messages and dicts | **Typed, versioned artifacts** (`Claim`, `Evidence`, `Answer`) |
| Orchestration is explicit wiring | Orchestration **falls out of the state** |
| A unit of work returns a change object | A step **returns typed artifacts**; the runtime compiles them |
| Retries/rollback are manual | Context is **git-like versioned** (diff, rollback, branch, merge) |
| “Who produced this?” is lost | **Provenance** links every derived artifact to its inputs |
| The model guesses the numbers | **Calculations are calculated** — the LLM reasons, Python computes |
| Tracing needs a SaaS add-on | **Native trace store** (SQLite + dashboard), exportable |
| Testing and evals need another stack | **Scenarios + evals + traces + replay** in one runtime |
| A web framework is the app | **One pipeline**, then CLI / HTTP / daemon / MCP adapters |
| Pulls in a framework's dependency tree | **3 core deps**: `pydantic`, `httpx`, `python-dotenv` |

Full breakdown, including where reactifact is *not* the right choice:
[docs/en/comparison.md](docs/en/comparison.md).

## Who this is for

- **Knowledge assistants over mixed sources.** One question spans Confluence, a
  spreadsheet and a web page; the next spans a different subset. You declare
  what artifacts exist and what agents can do with them, and the runtime derives
  the path. Figures are computed, answers cite their sources, and a run replays
  to the same hash.
- **Multi-step agents that don’t fit one graph.** Search, verification,
  calculation, human approval and outbound actions compose by type; the next
  question can take a different subset without rewiring.
- **Teams that need audit and regression gates.** Typed contracts, scenarios,
  dataset evals, provenance and deterministic replay are first-class, not a
  bolt-on.

**Not for a fixed A → B → C pipeline.** There a plain function or LangGraph is
shorter and just as correct.

**Not a connector library or a managed platform.** If you need dozens of
managed SaaS sources, LlamaIndex is the better tool. reactifact’s `Source` layer
is deliberately small and code-first: filesystem, CSV, embeddings, web, MCP.

## Proof in the repo

[`examples/fintech_audit`](examples/fintech_audit) — a transactions CSV, a budget
CSV and a policy doc, **no API key**. The model never produces the number; plain
Python does, and the answer links to its evidence:

```text
cloud spend:        $45,000   (2026-04 $12k, 2026-05 $15k, 2026-06 $18k)
variance vs budget: +12.5%    budget $40,000, policy threshold 10% → over
answer:             Q2 cloud spend was $45,000 against a $40,000 budget (+12.5%) — ...
citations:          budget.csv, transactions.csv, policy.md
audit:              Answer —supported_by→ {Variance, Spend, Table, Policy}
context sha256:     24449f6f…
```

![fintech_audit demo: the variance, the answer, and the reproducible context hash — a second run prints the same hash.](docs/img/fintech-audit-short.gif)

A harder, multi-source version — docs plus a CSV, computed sum, verified answer,
no key required — is [`examples/knowledge`](examples/knowledge):

![CLI demo: asking "how much does gpu cost in total?" — search, compute, verify, answer with sources.](docs/img/knowledge-cli-demo.gif)

Two strategies explore on separate forks and merge cleanly; if they edit the
same artifact differently, reactifact raises `MergeConflict` instead of guessing:

![forklab demo: two strategies investigate on separate forks and merge; a conflicting edit raises MergeConflict, then re-merges under an explicit policy.](docs/img/forklab-demo.gif)

## Optional knowledge layer

If the agent needs a corpus, `reactifact.knowledge` is a code-first ingestion
layer — `Connector → RawDocument → Document → Chunk → Index` — with incremental
`DocStore` state, BM25 + RRF hybrid retrieval, ACL-aware reconcile, and
`MemoryIndex` / `SqliteIndex` / `QdrantIndex` / `PgVectorIndex` (plus optional
`ChromaIndex`). It reuses the core `EmbeddingProvider`; `IndexSource` /
`IngestStep` are the only bridges into `Source` / `Step`.

```python
from reactifact.knowledge import KnowledgePipeline, SectionsChunker, SqliteIndex

pipeline = KnowledgePipeline(connector, chunker=SectionsChunker(), index=SqliteIndex(".reactifact/kb.db"))
await pipeline.run(incremental=True)
hits = await pipeline.query("how do I install it?", top_k=5, hybrid=True)
```

No YAML pipeline, CLI or plugin registry. See
[Knowledge base](docs/en/knowledge.md) (or [по-русски](docs/ru/knowledge.md)).

## In the box

- **Typed contracts** — `@step`, `Consume`, `When`, `Wakes`, `Scope`, joins and
  correlation; `validate_contracts` catches missing producers before a run.
- **Atomic state** — `Context` with git-like commits, `diff`, `rollback`,
  `branch`, three-way `merge`; artifacts are versioned by content hash.
- **Provenance** — `Answer —supported_by→ Claim —derived_from→ Evidence —
  extracted_from→ Doc`; walk it with `context.related(...)`.
- **HITL and outbox** — `human.ask(...)` → `PendingQuestion`; `outbox.act(...)`
  → `PendingAction`, delivered once after commit by `Runtime(dispatcher=...)`.
- **Traces and replay** — SQLite trace store + dashboard, portable
  Markdown/HTML/JSON reports, `ReplayLLM`, deterministic `context_hash`.
- **Scenarios and evals** — seeded scenarios, tool/resource faults, record/replay,
  assertions, golden runs, dataset metrics, judges and CI gates.
- **Budgets and lifecycle** — hard `max_seconds` deadlines, stop at a generation
  boundary, readiness via `active_runs()` / `in_flight`.
- **Sessions and chat** — `Chat` + `ChatAssistant` with `SessionStore`, SSE,
  history, memory policies, per-turn resources and `Chat.http()`.
- **Transports** — CLI, FastAPI (`/docs`, `/contracts`, `/traces`), daemon,
  MCP both ways (`create_mcp_server`, `mcp_stdio_tools`, `mcp_http_tools`).
- **Agent Skills** — bundled instructions for Claude Code, Cursor, Codex,
  Copilot: `reactifact`, `reactifact-agents`, `reactifact-llm`,
  `reactifact-rag`, `reactifact-testing`, `reactifact-eval`,
  `reactifact-observability`, `reactifact-from-langchain`.

## Start here

**Minimal agent, no web**

```bash
uvx reactifact new agent my-agent
cd my-agent && uv sync
uv run reactifact run main:pipeline "hello"
uv run reactifact trace
```

**Production HTTP shell**

```bash
uvx reactifact new app my-app
cd my-app && uv sync --extra dev
cp .env.example .env
uv run reactifact dev
```

**Existing project**

```bash
uv add reactifact
uv run reactifact skills install   # teach your coding agent the model
```

Then read [Quickstart](docs/en/quickstart.md) or [Concepts](docs/en/concepts.md).

## Examples (in-repo, not shipped)

- `starter_app` — copy-pasteable FastAPI app: structured call, RAG, tools, session chat.
- `knowledge` — multi-source chat: search → evidence → claim verification → answer, with CSV calculation.
- `knowledge_base` — real ingestion: connector → chunks → SQLite/Qdrant, incremental state, hybrid query.
- `medic_lab` — hypothesis laboratory: competing hypotheses scored, HITL steering, honest report.
- `devops` — HITL tool agents + LLM tool router + trace dashboard.
- `code_review` — deterministic-first diff review: rules + optional LLM findings → linked report.
- `repair` — budget-aware replanning (chat/data in Russian by design).
- `chat` — minimal offline single-model multi-turn conversation.
- `forklab` — deterministic branch & merge.
- `fintech_audit` — auditable, reproducible CSV answer with content hashes and provenance.

## Documentation

- [English](docs/en/index.md) · [Русский](docs/ru/index.md) — concepts, sources,
  providers, recipes, patterns, observability, eval, branching, replay, viz/CLI, API.
- [Quickstart](docs/en/quickstart.md) — demo, CLI, HTTP, daemon and MCP in a few lines.
- [Migrating](docs/en/migrating.md) — from LangGraph / CrewAI / LlamaIndex /
  plain Python: concept map, port-one-node, interop, checklist.
- [Why reactifact](docs/en/why-reactifact.md) — why the graph is derived rather
  than drawn, why determinism, why provenance.
- [Comparison](docs/en/comparison.md) — reactifact vs LangGraph/CrewAI, feature
  by feature, and when *not* to use reactifact.
- [docs/constitution.md](docs/constitution.md) — the design rationale and invariants.
- [Roadmap](docs/roadmap.md) — what’s next, and what’s deliberately out of scope.

## Development

```bash
uv sync --extra dev --extra web
.venv/bin/python -m pytest
.venv/bin/mypy
.venv/bin/ruff check
npx --yes pyright
```

## Status & contributing

Active, pre-1.0 — the API is stabilizing and `1.0` will freeze it (see the
[roadmap](docs/roadmap.md)); releases follow [Keep a Changelog](CHANGELOG.md).
Questions and design discussion live in
[GitHub Discussions](https://github.com/bzdvdn/reactifact/discussions); a star,
or a `good first issue`, helps more than you’d think.

## License

MIT — see [LICENSE](LICENSE).
