# Examples

Eight working applications ship in `examples/` (in-repo, not packaged). They are
the reference implementations for the [recipes](recipes.md),
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

**What it shows:** `fan_out_sources` search over file + CSV sources → lazy
`materialize_doc` → evidence extraction → claim verification → answer with
provenance; deterministic CSV calculation (`Spreadsheet` → `Calculation`).

```bash
uv run python ./examples/knowledge/web.py    # FastAPI/SSE + trace dashboard
uv run python ./examples/knowledge/chat.py   # interactive CLI
```

Key files: `steps/` (common, router, search, evidence, calc, lifecycle),
`agents.py`, `web.py`.

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
own); trace dashboard with the `create_trace_router` UI. It also carries the
trust & safety layer: `guardrails.py` defines a **custom** guardrail (a
destructive production change must cite a `CHG-` ticket) plus secret
redaction, wired onto `RuntimeResources` in one line — the ops policy blocks
injection and unapproved prod changes, redacts PII/secrets, and screens the
raw input too. And it scores its own traffic: `online_eval.py` samples served
runs from the trace store (`trace_source`, custom `routed`/`routed_to`
evaluators), tags passes `eval` / failures `eval:failed`, and `web.py` mounts
`create_online_eval_router` (`POST /api/evals/run`). Run the standalone demos
(no LLM, no network):

```bash
uv run python ./examples/devops/web.py      # SSE UI + trace dashboard
uv run python ./examples/devops/safety.py   # custom guardrails, end to end
uv run python ./examples/devops/online_eval.py   # online-eval over served runs
```

## `repair` — budget-aware replanning (Russian by design)

**What it shows:** chat and catalog data are intentionally **Russian** — the
contrast with other English examples is deliberate (localization is a product
concern, not a framework one). The flow: fact collection → LLM design options →
3 photo previews → plan → **deterministic catalog estimate** → HITL approval →
budget complaint triggers a rebuild from an earlier stage with a `_downstream_resets`
rollback. Everything explicit is deterministic; only genuinely generative steps
use the LLM.

```bash
uv run python ./examples/repair/web.py
uv run python ./examples/repair/chat.py
```

## `chat` — minimal single-model multi-turn conversation (offline)

**What it shows:** the simple multi-turn shape in ~80 lines — one message model
(`ConversationMessage`) both sides write. `Conversation` records turns
(`create_turn`) and renders the thread (`transcript`/`state`); `ChatMemory` with
`keep=` bounds it (`WindowPruner`) — wired through `ChatAssistant`. No LLM, no
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

See also [`reactifact/audit.py`](../reference.md) (`build_report`,
`context_hash`, `report_to_markdown`) and [Observability](observability.md) for
shipping such a run to a sink with `redactor=`.

## Running tests

```bash
.venv/bin/python -m pytest      # full suite (PostgreSQL cases skip without TEST_PG_DSN)
.venv/bin/mypy                  # strict typing across the repo
.venv/bin/ruff check            # lint
```

`uv sync` installs the dev+web groups from `pyproject.toml`; `uv build` ships
only the `reactifact` wheel (examples and docs are not packaged).
