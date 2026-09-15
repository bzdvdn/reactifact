# Examples

Fifteen working applications ship in `examples/` (in-repo, not packaged). They
are the reference implementations for the [recipes](recipes.md),
[patterns](patterns.md) and the [port matrix](port-matrix.md) — canonical
examples are split into a `produce/` package (stages) + thin
`web.py`/`chat.py`/`main.py` entries.

All demos run **with or without API keys**: configure `.env` (copy
`.env.example`) for LLM/embedder/image providers, or let them fall back to
deterministic demo mode.

The session-chat demos (`knowledge`, `devops`, `research`, `repair`) build
their web layer on the canonical `reactifact.chat` + `reactifact.web` contract —
each supplies only the domain hooks (agents, input model, terminal reply),
the SSE transport and session persistence come from the framework. Ports
(`reflection` …) and `adaptive` are CLI-only by design.

## Tutorial ladder

- `llm_ladder` — the **recommended starting point**: the LLM workflow from a
  single call (level 1) through patching artifacts (level 2) to a full
  state-changing session with lifecycle (level 3). Self-contained, offline
  fallbacks, model mode via `.env`. See [index](index.md#llm-ladder).

```bash
uv run python ./examples/llm_ladder/level1.py
```

## `knowledge` — multi-source chat with evidence

**What it shows:** `fan_out_sources` search over file + CSV sources → lazy
`materialize_doc` → evidence extraction → claim verification → answer with
provenance; deterministic CSV calculation (`Spreadsheet` → `Calculation`).

```bash
uv run python ./examples/knowledge/web.py    # FastAPI/SSE + trace dashboard
uv run python ./examples/knowledge/chat.py   # interactive CLI
```

Key files: `produce/` (common, router, search, evidence, calc, lifecycle),
`agents.py`, `web.py`.

## `research` — goes to the web

**What it shows:** `WebSource` with real URL discovery; *lazy* page resolution
only for pages the model ranks relevant (§6); evidence → verified claims →
answer with URL provenance; `StatusMachine` turn lifecycle.

```bash
uv run python ./examples/research/web.py
uv run python ./examples/research/chat.py
```

## `medic-lab` — hypothesis laboratory

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
own); trace dashboard with the `create_trace_router` UI.

```bash
uv run python ./examples/devops/web.py
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

The same pattern is the natural base for rewriting `medic-lab`: hypotheses
become real forks instead of tag-routed channels.

## `ledger` — minimal recompute (reactive dependency graph)

**What it shows:** a tiny cost model (hours × rate → labor cost → tax/discount
→ total), built to make one point concretely, with numbers: dependencies are
declared on artifact *types* via `Consume`, not wired between nodes sharing one
blob of state — so editing one fact recomputes exactly what actually consumed
it, and nothing else. `Artifact.version` is the proof: a formula that never
consumed the edited fact is never even invoked for that edit, not
"invoked-and-decided-not-to-update." No LLM, fully deterministic (§67) — same
offline-proof spirit as `forklab`, but for reactive invalidation instead of
branch/merge.

```bash
uv run python -m examples.ledger.main
```

## `adaptive` — hybrid scheduling

**What it shows:** the adaptive planner (`reactifact.scheduler`) in action — hard
filter rules prune capabilities, a deterministic metric ranks the rest, an
optional LLM breaks ties, `rank_limit` caps the number of agents that actually
run; HITL-enabled agents are pinned, none starve.

```bash
uv run python -m examples.adaptive.main            # "money" → picked: b
uv run python -m examples.adaptive.main --tag x    # rule prunes b entirely
```

## Canonical ports (offline-capable mini-demos)

Small, self-contained ports of the classic agent patterns — every one runs
`uv run python -m examples.<name>.main` and is mapped in the
[port matrix](port-matrix.md). `plan_execute`, `supervisor`, and `reflection`
each also ship a `main_recipe.py` running the identical scenario on the
corresponding `reactifact.recipes` class (`python -m examples.<name>.main_recipe`)
— compare the two to see exactly what the recipe takes off your hands.

- `reflection` — generate → critique → regenerate a draft until a guard passes.
- `map_reduce` — chunk a document, per-chunk produces, then aggregate
  (`fan_out` + `combine`).
- `supervisor` — a supervisor delegates *specialist* produces (HITL approvals).
- `summarize` — conversation memory summarization (short → long window).
- `time_travel` — `Context.branch()`, run two strategies in parallel,
  three-way `merge()`.
- `plan_execute` — planner drafts ordered steps, executor runs exactly one
  per generation gated on the previous step's result, finisher synthesizes
  the answer once every step has one; see [patterns](patterns.md#plan-and-execute-draft-once-run-one-step-at-a-time).

## Running tests

```bash
.venv/bin/python -m pytest      # 541 tests (2 skipped without TEST_PG_DSN)
.venv/bin/mypy                  # strict typing across the repo
.venv/bin/ruff check            # lint
```

`uv sync` installs the dev+web groups from `pyproject.toml`; `uv build` ships
only the `reactifact` wheel (examples and docs are not packaged).