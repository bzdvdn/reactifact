<p align="center">
  <img src="docs/img/reactifact-hero.png" alt="reactifact — Agents that react to artifacts, not graphs" width="800">
</p>

**Stop drawing the graph. Build agents as reactions to versioned, provable artifacts.**

[![CI](https://github.com/bzdvdn/reactifact/actions/workflows/ci.yml/badge.svg)](https://github.com/bzdvdn/reactifact/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/bzdvdn/reactifact/graph/badge.svg)](https://codecov.io/gh/bzdvdn/reactifact)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://github.com/bzdvdn/reactifact)
[![PyPI version](https://img.shields.io/pypi/v/reactifact)](https://pypi.org/project/reactifact/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/bzdvdn/reactifact)
[![Docs](https://img.shields.io/badge/docs-bzdvdn.github.io%2Freactifact-blue)](https://bzdvdn.github.io/reactifact/)

Most agent frameworks make you **draw the graph**: connect nodes, wire memory,
declare control flow. But a knowledge question — *"why did infra costs jump in
Q2?"* — needs Confluence + GitLab + CSV + calculations + verification, and the
*next* question needs a different path. There is no universal graph to draw.

reactifact flips the model. You describe **what artifacts exist and what agents can
do with them**; the runtime derives what runs next from **state changes**. Agents
react to events — there is no graph, no node pipeline.

![Left: a hand-wired fetch → verify → answer pipeline. Right: reactifact — search_agent and answer_agent each declare only what they consume and produce, wired together by Context, never each other.](docs/img/wiring.svg)

Two agents explore independently on their own forks and merge back automatically
— and when they disagree, reactifact refuses to merge silently:

![forklab demo: two strategies (depth/breadth) investigate on separate forks and merge cleanly; a second run edits the same artifact on both forks and reactifact raises MergeConflict instead of guessing, then re-merges under an explicit policy.](docs/img/forklab-demo.gif)

```bash
pip install reactifact
```

Runs offline, no API key needed — paste this straight into a `.py` file. Two
agents, no graph edge declared between them — the second reacts because the
first one's output exists, and the answer carries *proof* of where it came
from:

```python
from pydantic import BaseModel

from reactifact import Budget, Consume, Context, Runtime, RuntimeResources, create_agent, produce


class Question(BaseModel):
    text: str


class Evidence(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


DOCS = {
    "refund": "Refunds are available within 14 days of purchase.",
    "pricing": "The Pro plan is $49/month, billed annually.",
}


@produce(Evidence)
async def find_evidence(context, inputs, event, effects):
    question = next((a for a in inputs if isinstance(a.data, Question)), None)
    if question is None:
        return None
    hit = next((v for k, v in DOCS.items() if k in question.data.text.lower()), None)
    if hit is not None:
        effects.create(Evidence(text=hit))


@produce(Answer)
async def answer_from_evidence(context, inputs, event, effects):
    evidence = next((a for a in inputs if isinstance(a.data, Evidence)), None)
    if evidence is None:
        return None
    effects.create(Answer(text=evidence.data.text)).link("supported_by", evidence)


search_agent = create_agent("search", consumes=[Consume(Question)], produces=[find_evidence])
answer_agent = create_agent("answer", consumes=[Consume(Evidence)], produces=[answer_from_evidence])

ctx = Context(resources=RuntimeResources())
runtime = Runtime(ctx, agents=[search_agent, answer_agent], budget=Budget(max_runs=10))

ctx.create(Question(text="what's your refund policy?"))
runtime.run()  # search_agent and answer_agent both react — nobody wired them together

answer = ctx.latest(Answer)
evidence = ctx.related(answer.id, "supported_by")[0]
print(answer.data.text)                     # "Refunds are available within 14 days of purchase."
print("supported_by:", evidence.data.text)  # provenance you can trace, not just a string in a log
```

The same idea, live — the [`knowledge`](examples/knowledge) example's CLI answering a
harder, multi-source question (docs + a CSV) with a real computed number and
its sources, no LLM key required:

![CLI demo: asking "how much does gpu cost in total?" — the runtime searches docs and a spreadsheet, computes the sum, verifies it, and answers with sources.](docs/img/knowledge-cli-demo.gif)

## How it works

```text
ARTIFACT CREATED / UPDATED
       │
       ▼
     AGENTS REACT ──self.effects──► Effects ──compile──► Patch
       ▲                                                      │
       └──────────────────────────────────────────────────────┘
                                                        Context v+1
```

A produce writes what should change (`self.effects.create/update/link/ask`) and
returns `None`; the runtime compiles the effect set into one **atomic** `Patch`
and moves the context to the next version. The `event` that wakes an agent is
*derived* from that same change — the causal chain can never drift from the
actual state.

## What makes it different

| Traditional agent (LangGraph / CrewAI / LangChain) | reactifact |
| --- | --- |
| A program follows a graph / plan | Agents **react** to state changes |
| Messages are strings | **Typed, versioned artifacts** (`Claim`, `Evidence`, `Answer`) |
| Orchestration is explicit wiring | Orchestration **falls out of the state** |
| A unit of work *returns* a change | An agent **writes effects**; the runtime compiles them |
| Retries/rollback are manual | Context is **git-like versioned** (diff, rollback, branch, merge) |
| "Who produced this?" is lost | **Provenance** links every derived artifact to its inputs |
| The model guesses the numbers | **Calculations are calculated** — the LLM is a reasoning component, not the source of truth |
| Tracing needs a SaaS add-on | **Native trace store** (SQLite + dashboard), exportable to Langfuse/Postgres |
| MCP via a framework adapter | **MCP both ways** built in — call any server, or expose your own `Context` as one |
| Pulls in a framework's dependency tree | **3 core deps**: `pydantic`, `httpx`, `python-dotenv` |

Reactive. Deterministic. Accountable.

Full breakdown, including where reactifact is *not* the right choice:
[docs/en/comparison.md](docs/en/comparison.md).

**Proof, not a claim** — [`examples/ledger`](examples/ledger) is a 4-artifact
billing calc (`LaborCost`, `Tax`, `Discount`, `Total`) with no LLM, fully
offline. Edit *one* fact and see what actually reruns:

```text
>>> editing ONLY TaxRate (0.08 -> 0.12) — a fact nothing about
>>> LaborCost or Discount ever consumed.

  LaborCost    value=500.0      version=0   # untouched
  Tax          value=60.0       version=1   # recomputed
  Discount     value=25.0       version=0   # untouched
  Total        value=535.0      version=1   # recomputed
```

2 of 4 artifacts recompute — the 2 that actually depend on `TaxRate` —
because `Artifact.version` tracks real consumption, not a graph edge you drew
by hand. Run it yourself: `uv run python -m examples.ledger.main`.

## Core primitives

- **Context** — versioned working state, git-like commits, `diff`/`rollback`/`merge`.
- **Artifact** — a first-class typed object (`Claim`, `Evidence`, `Answer`), not a string blob.
- **Effects** — an agent states its change via `self.effects.create/update/link/ask`; the runtime compiles it.
- **Patch** — the compiled, validated change-set applied as one atomic commit.
- **Agent** — a thin container declaring `consumes`/`produces`; logic lives in a `Produce`.
- **Source** — retrieval is a capability: vector search is *one* strategy, not the only one; filesystem, CSV, and the web are equally first-class today (direct API, keyword, and SQL sources are on the [roadmap](docs/roadmap.md#next), not yet shipped).
- **Provenance** — every derived artifact links to what produced it
  (`Answer —supported_by→ Claim —derived_from→ Evidence —extracted_from→ Doc`).
- **HITL** — humans as `effects.ask(...)` → `PendingQuestion`, answered via `effects.resume(...)` like any agent.

## In the box

- **Deterministic by design** — calculations over structured data, honest `None`
  fallbacks instead of hallucinated answers; the model reasons, never "knows".
- **Observability** — every run traces agent spans, reads/writes, LLM calls,
  tokens: SQLite store + web dashboard, exportable to Langfuse/Postgres (async sinks).
- **MCP, both ways** — call any MCP server's tools as a `Tool`
  (`mcp_stdio_tools`/`mcp_http_tools`), or expose your own `Tool`s and a live
  `Context` as an MCP server (`create_mcp_server`) for Claude Desktop, Claude
  Code, or another agent to call into (`mcp` extra).
- **Budgets & replanning** — cap by runs/time/iterations/tool-calls, replan on decline.
- **Branching & replay** — `context.branch()`, three-way `merge()`, deterministic
  `ReplayLLM`, all for audit and safe alternative states.
- **Sessions** — `SessionStore` over `FileKVBackend`/`SQLiteKVBackend`
  (and `PostgreSQLKVBackend`) for durable chat memory.
- **Web layer** — `ChatAssistant` + `create_chat_router` mount a canonical SSE
  chat on *your* FastAPI app; errors degrade to a logged fallback, never a 500.
- **Recipes** — `find`/`find_all` (typed lookup in `inputs`), `fan_out_sources`,
  `materialize_doc`, `StatusMachine`, `WindowSummarizer`/`WindowPruner`
  (bounded conversation memory), change→rebuild rollback helpers,
  `Skill`/`match_skills` (Claude-Skills-shaped instructions, keyword-triggered)
  — pure and LLM-free, except the summarizer, which takes your callback.
- **Viz & CLI** — Mermaid `blueprint`/`context_to_mermaid`/`trace_to_mermaid`;
  `reactifact` with `graph`/`context`/`trace`/`replay`/`branch`.

## Run a demo

Offline-capable, no API keys required (deterministic fallbacks):

```bash
uv run python ./examples/llm_ladder/level1.py  # the simplest LLM turn (offline too)
uv run python ./examples/repair/web.py         # room renovation: plan, estimate, CSV export
uv run python ./examples/devops/web.py         # HITL ops assistant + trace dashboard
```

Classic-pattern ports run as one-liners too:
`python -m examples.{reflection,map_reduce,supervisor,summarize,time_travel,plan_execute,adaptive,ledger}.main`.

## Examples (in-repo, not shipped)

- `knowledge` — multi-source chat: search → evidence → claim verification → answer, with CSV calculation.
- `research` — goes to the web (`WebSource`): lazy page fetch → evidence → verified claims → answer with URL provenance.
- `medic-lab` — hypothesis laboratory: competing hypotheses scored, HITL steering, honest report.
- `devops` — HITL tool agents + LLM tool router + trace dashboard.
- `repair` — budget-aware replanning (chat/data in Russian by design).
- `forklab` — deterministic branch & merge: two strategies on their own forks, three-way merge.
- `ledger` — offline proof of reactive recompute: edit one fact, only its real `Consume`rs re-run.
- `llm_ladder` — the workflow from one LLM call to state-changing patches (3 levels).
- `adaptive` — hybrid scheduler: rule filters + deterministic rank + LLM tie-break + `rank_limit`.
- `{reflection,map_reduce,supervisor,summarize,time_travel,plan_execute}` — canonical ports (see [port-matrix](docs/en/port-matrix.md)).

## Documentation

- [English](docs/en/index.md) · [Русский](docs/ru/index.md) — concepts, sources,
  providers, recipes, patterns, observability, eval, branching, replay, viz/CLI, API.
- [Quickstart](docs/en/quickstart.md) — three runnable snippets: tool-calling
  agent, retrieval over your docs, session-persisted chat bot.
- [Why reactifact](docs/en/why-reactifact.md) — the *design argument*: why effects, why no graph, why determinism.
- [Comparison](docs/en/comparison.md) — reactifact vs LangGraph/CrewAI, feature by feature, and when *not* to use reactifact.
- [Tutorial · llm-ladder](docs/en/examples.md#tutorial-ladder) — learn the workflow.
- [docs/constitution.md](docs/constitution.md) — the full design rationale and invariants.
- [Roadmap](docs/roadmap.md) — what's next, and what's deliberately out of scope.

## Development

```bash
uv sync --extra dev --extra web
.venv/bin/python -m pytest
.venv/bin/mypy
.venv/bin/ruff check
```

## License

MIT — see [LICENSE](LICENSE).
