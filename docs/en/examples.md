# Examples

Thirteen runnable examples ship in `examples/` (in-repo, not packaged). Twelve
are full applications; `tour` isolates the primitives. They are the reference
implementations for the [recipes](recipes.md),
[patterns](patterns.md) and the [port matrix](port-matrix.md) — canonical
examples are split into a `steps/` package (stages) plus thin
`web.py`/`chat.py`/`main.py` entries.

All demos run **with or without API keys**: configure `.env` (copy
`.env.example`) for LLM/embedder/image providers, or let them fall back to
deterministic demo mode.

The session-chat demos (`knowledge`, `devops`, `repair`) build their web layer
on the canonical `reactifact.chat` + `reactifact.web` contract — each supplies
only the domain hooks (agents, input model, terminal reply), and the SSE
transport and session persistence come from the framework.

## What each example teaches

| Example | What it teaches | Shape |
| --- | --- | --- |
| `starter_app` | minimal FastAPI app over the `quick` cases | on-ramp; no auth/settings |
| `knowledge` | multi-source RAG, evidence and deterministic CSV calculation | domain web app |
| `knowledge_base` | connector → chunks → index, incremental state, hybrid query | ingestion/CLI |
| `medic_lab` | competing hypotheses, scoring, HITL steering | domain service |
| `devops` | HITL tools, authz, per-user memory, traces | closest to production; demo auth |
| `code_review` | deterministic diff rules + optional LLM, linked report | CLI/worker |
| `repair` | stage-driven replanning, budget, HITL | domain web app |
| `chat` | minimal transcript + bounded `ChatMemory` | offline chat |
| `forklab` | `branch`/`merge`, conflicts and policies | CLI/state exploration |
| `fintech_audit` | computed, reproducible answer with context hash | CLI/auditable pipeline |
| `outbox` | committed side effects, retry and replay | worker/daemon pattern |
| `repo_agent` | destructive tool behind approval | CLI/guardrail |
| `tour` | framework primitives in small scenes | learning |

Use this table to pick the example closest to your problem. If you read only
three, start with `starter_app` (smallest web), `devops` (tools/authz/memory)
and `fintech_audit` (computed, reproducible answer). Examples stay deliberately
focused; they do **not** all carry auth/settings. The production-shaped scaffold
is `reactifact new app|daemon`, not another example.

## `starter_app` — a copy-pasteable web app over the quick cases

**What it shows:** one FastAPI process you can clone and run, covering the
shapes most apps start with — a structured call, RAG with citations, an LLM with
tools, and a session-persisted chat — plus a trace dashboard for all of them.
It's a thin skin over [`reactifact.quick`](quickstart.md#0-the-80-on-ramp-reactifactquick),
and the provider is chosen by `from_env()`: no key → offline/deterministic
fallbacks (RAG still answers from `knowledge/` with citations); `OPENROUTER_API_KEY`
→ OpenRouter; `OPENAI_BASE_URL` → any OpenAI-compatible endpoint. Copy the
directory and swap the domain.

```bash
uv sync --extra web
uv run python -m examples.starter_app.app      # http://127.0.0.1:8000
```

## `knowledge` — multi-source chat with evidence

**What it shows:** `fan_out_sources` over a `reactifact.knowledge` index + a
structured CSV source → lazy `materialize_doc` → evidence extraction → claim
verification → answer with provenance; deterministic CSV calculation
(`Spreadsheet` → `Calculation`). The web app also mounts `/traces` (agent runs
with Question/Answer columns), `/contracts` (static consumes/produces/tools) and
`/knowledge/` (ingestion status).

```bash
uv run python ./examples/knowledge/web.py    # FastAPI/SSE + trace dashboard
uv run python ./examples/knowledge/chat.py   # interactive CLI
uv run python -m examples.knowledge.eval    # offline retrieval metrics
```

Key files: `steps/` (common, router, search, evidence, calc, lifecycle),
`agents.py` (the `Agent` definitions), `web.py`.

## `knowledge_base` — code-first ingestion and retrieval

**What it shows:** the optional `reactifact.knowledge` layer over a real corpus:
`Connector -> RawDocument -> Document -> Chunk -> EmbeddingProvider -> Index`,
incremental `DocStore` state, and hybrid (BM25 + RRF) retrieval. Defaults to
SQLite and a deterministic embedder, so it runs offline; `--qdrant-url` switches
the index without changing the pipeline.

```bash
uv run python examples/knowledge_base/build.py --docs docs/en --query "sources"
uv run python examples/knowledge_base/build.py --docs docs/en --store sqlite
uv run python examples/knowledge_base/build.py --docs docs/en --qdrant-url http://localhost:6333
```

Key files: `build.py` (connector, pipeline wiring, query). See
[Knowledge base](knowledge.md).

## `medic_lab` — hypothesis laboratory

**What it shows:** one question spawns competing hypotheses; each is
investigated over an evidence pool; **scored** by support/contradiction counts
(scores are calculated, not guessed); HITL steering via `PendingQuestion`;
honest report when evidence is insufficient; `concurrency_limit` (LLM agents = 2,
global cap = 6).

```bash
uv run python ./examples/medic_lab/main.py    # serves uvicorn automatically
```

## `devops` — HITL tool agent (ops assistant)

**What it shows:** `HITLLMAgent` + LLM **tool router** (routing is a separate
structured step, `StructuredLLM`), HITL-approved mutations for Kubernetes,
GitLab, Ansible (each is a fateful operation the LLM cannot dream up on its
own); trace dashboard with the `create_trace_router` UI and a `/contracts`
page. It also carries the
trust & safety layer: `guardrails.py` defines a **custom** guardrail (a
destructive production change must cite a `CHG-` ticket) plus secret
redaction, wired onto `RuntimeResources` in one line — the ops policy blocks
injection and unapproved prod changes, redacts PII/secrets, and screens the
raw input too. And it scores its own traffic: `online_eval.py` samples served
runs from the trace store (`trace_source`, custom `routed`/`routed_to`
evaluators), tags passes `eval` / failures `eval:failed`, and `web.py` mounts
`create_online_eval_router` (`POST /api/evals/run`). Two demo identities show
the multi-user layer: `admin1` (admin) and `admin2` (analyst), picked in the UI
and sent as `X-User` (demo authentication only). Each runs with a
`Principal` + `PermissionPolicy` (`policy.py`), so `admin2` can chat, use k8s and
GitLab but is refused before the Ansible agent starts. Long-term memory is
per-user (`MemoryStore(namespace="user:<name>")`): a report summary is recorded
as an outbox `PendingAction` (`remember_later`) and indexed after the
report commits (`MemoryStore.dispatch`), so a failed turn cannot leave a
dangling entry; recall is folded into routing in later sessions, never copied
from the chat history, and never visible in the other user's namespace. Run
the standalone demos (no LLM, no network):

```bash
uv run python ./examples/devops/web.py      # SSE UI + trace dashboard
uv run python ./examples/devops/safety.py   # custom guardrails, end to end
uv run python ./examples/devops/online_eval.py   # online-eval over served runs
```

## `code_review` — deterministic-first review agent

**What it shows:** a unified diff → `ChangedFile` → per-file `FileReview`
(deterministic rules plus an optional LLM) → `ReviewReport` with Markdown
findings. Rules catch `eval`/`exec`, `shell=True`, hardcoded secrets, bare
`except`, `TODO` and debug `print`; the report links back to every reviewed
file (`reviewed` relation). Runs offline with no API key.

```bash
python -m examples.code_review.main --demo
git diff | python -m examples.code_review.main
```

## `repair` — budget-aware replanning (Russian by design)

**What it shows:** chat and catalog data are intentionally **Russian** — the
contrast with other English examples is deliberate (localization is a product
concern, not a framework one). The flow: fact collection → LLM design options →
3 photo previews → plan → **deterministic catalog estimate** → HITL approval →
budget complaint triggers a rebuild from an earlier stage with a `_downstream_resets`
rollback. Everything explicit is deterministic; only genuinely generative steps
use the LLM.

The web app mounts `/traces` and `/contracts` next to the chat.

```bash
uv run python ./examples/repair/web.py
uv run python ./examples/repair/chat.py
```

## `chat` — minimal single-model multi-turn conversation (offline)

**What it shows:** the simple multi-turn shape in ~80 lines — one message model
(`ConversationMessage`) both sides write. `Conversation` records turns
(`create_turn`) and renders the thread (`transcript`/`state`); `ChatMemory` with
`keep=` bounds it (`WindowPruner`) — wired through `Chat`. No LLM, no
API key (a deterministic echo agent answers). The heterogeneous shape (separate
question/answer types) is in the `repair`/`devops`/`knowledge` examples, via
`Transcript`/`MessageSpec` — see [Multi-turn & chat](chat.md).

```bash
.venv/bin/python -m examples.chat.main
```

## `forklab` — branch & merge (§39-§40)

**What it shows:** deterministic alternative-state exploration — one question,
two research strategies on **their own forks** (`Context.branch()`); a
three-way `merge()` that either unions cleanly or raises an explicit
`MergeConflict` (§40, never a silent choice); an evaluator over the merged
state whose `Answer` is linked `supported_by` to findings from **both**
branches. Fully offline (§67) — the point is the state semantics, not a model.
A `--conflict` flag demonstrates the conflict-and-policy-resolve loop.

```bash
uv run python -m examples.forklab.main            # happy path
uv run python -m examples.forklab.main --mermaid  # merged provenance graph
uv run python -m examples.forklab.main --conflict # explicit MergeConflict + policy
```

The same pattern is the natural base for rewriting `medic_lab`: hypotheses
become real forks instead of tag-routed channels.

## `fintech_audit` — auditable, reproducible answer

**What it shows:** a finance question (*"what is the Q2 cloud spend variance,
and does policy require approval?"*) answered from a transactions CSV, a budget
CSV and a policy document. The audit story, end to end: the figures are computed
in plain Python (the model is never the source of truth, §67); every derived
artifact links to what it came from; `reactifact.audit.build_report` renders the
answer with a content hash per artifact and a `context_sha256` for the run; and
re-running the pipeline hashes identically — the check
`reactifact replay <store> --session <id> --verify <hash>` runs against a saved
session. Offline, no key.

```bash
uv run python -m examples.fintech_audit.main
```

![fintech_audit demo: the variance and its audit report, then the reproducibility check — a second run prints the same context hash.](../img/fintech-audit-demo.gif)

See also [`reactifact/audit.py`](../reference/index.md) (`build_report`,
`context_hash`, `report_to_markdown`) and [Observability](observability.md) for
shipping such a run to a sink with `redactor=`.

## `outbox` — external effects as committed state

**What it shows:** the outbox contract in seven deterministic cases — a step
records `outbox.act(...)` instead of doing I/O; `Runtime(dispatcher=...)` sends
after the commit; re-derivation, same-generation duplicates, merged branches,
replay, failure→retry and an app-owned retry wrapper all keep one delivery.
No LLM, no network; the fake dispatcher makes the count visible.

```bash
uv run python -m examples.outbox.main
```

See [Outbox in production](durability.md#outbox-in-production) for the
at-least-once contract and the app-owned drain/retry policy.

## `repo_agent` — a destructive tool behind approval

**What it shows:** a `HITLLMAgent` coding assistant over a tiny repository.
`read_file` and `run_tests` run immediately; `git_commit` is
`@tool(destructive=True)`, so the runtime turns the model's decision into a
`PendingQuestion(kind="approve")` and executes only after `context.resume(...)`.
The provider is scripted, so the demo is offline and deterministic.

```bash
uv run python -m examples.repo_agent.main
```

## `tour` — the framework primitives in seventeen small scenes

**What it shows:** the parts that are easy to miss when reading a full app:
typed artifacts with `version_history` / `diff` / `compact`; typed inputs with
`Latest`, implicit read-only inputs and `Consume(..., debounce=True)`; a SQLite
session that survives a restart, resumes a pending event and replays an earlier
commit;
an optional LLM step; agent retries and per-step deadlines; a schema-backed HITL
question resumed with a Pydantic model; checkpoints/forks; the typed
`Runtime.astream_events` modes; an optimistic-write conflict resolved by a
merge policy; and a subflow on an isolated Context with a parent-run trace link,
plus a MapReduce fan-out/reduce, a peer-to-peer swarm handoff, tool
record/replay via `Runtime.replay_run`, an A/B experiment with a winner, a static
contract validation, namespaced long-term memory, and an offline knowledge
pipeline with hybrid retrieval and metrics. Every scene is offline by
default, and the `model` scene runs with a provider only when `--llm` is passed.

```bash
uv run python -m examples.tour.main                 # all scenes, offline
uv run python -m examples.tour.main --scene state   # versions + compaction
uv run python -m examples.tour.main --scene durable --db /tmp/tour.sqlite3
uv run python -m examples.tour.main --scene resilience
uv run python -m examples.tour.main --scene hitl
uv run python -m examples.tour.main --scene knowledge
uv run python -m examples.tour.main --scene time_travel
uv run python -m examples.tour.main --scene events
uv run python -m examples.tour.main --scene conflicts
uv run python -m examples.tour.main --scene subflow
uv run python -m examples.tour.main --scene map_reduce
uv run python -m examples.tour.main --scene swarm
uv run python -m examples.tour.main --scene replay
uv run python -m examples.tour.main --scene experiment
uv run python -m examples.tour.main --scene validate
uv run python -m examples.tour.main --scene memory
uv run python -m examples.tour.main --llm --scene model
```

With `--llm`, `from_env()` reads `examples/tour/.env` (copy `.env.example`);
without a provider the model scene prints an honest deterministic brief. See
[`examples/tour`](../../examples/tour).

## Running tests

```bash
.venv/bin/python -m pytest      # full suite (PostgreSQL cases skip without TEST_PG_DSN)
.venv/bin/mypy                  # strict typing across the repo
.venv/bin/ruff check            # lint
```

`uv sync` installs the dev+web groups from `pyproject.toml`; `uv build` ships
only the `reactifact` wheel (examples and docs are not packaged).
