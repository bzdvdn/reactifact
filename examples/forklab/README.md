# fork-lab

Deterministic **branch & merge** demonstration (§39-§40) that also shows the
model working *within* the state flow — not just once, but throughout the
pipeline.

The branching, scoring and provenance are **deterministic** (§67): which
documents each strategy picks, their scores, the three-way merge, and the
`supported_by` links never depend on a model. The LLM is used only where
generation is genuinely needed (§68): wording each finding (one call on the
depth branch, three on the breadth branch) and synthesizing the final answer
over the merged state.

![A base context holding Question and Budget forks into a depth and a breadth strategy agent; the two forks merge back with a three-way merge, and the final Answer stays linked supported_by evidence from both branches.](../../docs/img/forklab-flow.svg)

The top-level README's demo GIF (`docs/img/forklab-demo.gif`) is this example's
`main.py`/`--conflict`/`--mermaid` output, recorded with
[VHS](https://github.com/charmbracelet/vhs) — re-render it after a CLI output
change with `vhs docs/img/forklab-demo.tape` (from the repo root).

## Run

```bash
uv run python -m examples.forklab.main            # offline mode (no .env)
uv run python -m examples.forklab.main --mermaid  # merged provenance graph
uv run python -m examples.forklab.main --conflict # explicit MergeConflict (§40)
uv run python ./examples/forklab/web.py           # FastAPI/SSE chat (uvicorn)
```

The web app streams the whole pipeline (`status` events: forking, per-branch
wording, merging, synthesizing) and then renders the answer with its sources and
the merged provenance graph (mermaid) live in the page.

- **Offline mode** — no `.env`: every LLM step degrades honestly to the
  deterministic fallback (§59); the whole flow still runs.
- **Model mode** — set `LLM_PROVIDER` (+ key/model) in `.env` (see
  `.env.example`): the run auto-switches and calls the model **5 times** —
  findings on both branches + the final synthesis. With a real vendor the call
  schedule is visible in the run trace.

Output includes the mode, merged version, the per-branch evidence split, the
artifact budget, the answer, and provenance link count.

## What it demonstrates

- **`Context.branch()`** — isolated copies for alternative research strategies;
  changes on one fork never leak into the other.
- **Three-way `merge()`** — union of both forks' findings; the shared
  `budget:1` artifact is untouched on the happy path, so the merge is clean.
- **Atomic conflicts (§40)** — with `--conflict`, both forks edit `budget:1`
  differently; `merge()` raises `MergeConflict` *before applying anything*, and
  the demo then shows a policy ("keep depth") re-merging cleanly. The framework
  never chooses silently.
- **Evaluate on the merged state** — the `Review` trigger runs `Evaluate` over
  the merged context; the `Answer` is linked `supported_by` to evidence from
  **both** branches (provenance is first-class, §34).
- **LLM throughout the flow, not just once** — findings on each branch and the
  answer synthesis are model-made; scoring, merge and provenance are not.
- **The model knows the domain (§68)** — every prompt is built from the live
  `Question` (see `prompts.py`): the *topic* and the exact *question* are in the
  system prompt, so wording and synthesis never work without context.
- **Deterministic by design (§67)** — same inputs → same merged context, which
  is exactly what replay (§55) and differential testing rely on.

## Structure

```
examples/forklab/
  models.py          Question(topic), Strategy, Evidence(Body), Review, Answer(Body), Budget
  prompts.py         PromptTemplate-based SYSTEM_WORDING / SYSTEM_SYNTHESIS — every call knows the topic + question
  produce/
    investigate.py   DepthInvestigate / BreadthInvestigate — deterministic pick + LLM wording
    evaluate.py      Evaluate — deterministic ranking/links, LLM/fallback synthesis
  agents.py          StrategyAgent (per fork) · EvaluatorAgent (on merged)
  pipeline.py        application orchestration (fork → run → merge → Review → evaluate)
  main.py            CLI entry — arguments + printing only
  web.py             FastAPI/SSE app streaming the pipeline + merged graph
  web/index.html     chat page (SSE progress, answer, mermaid diagram)
  .env.example       optional model mode
```

## Extension point

The same pattern is a natural fit for a real hypothesis lab: `medic-lab` today
tag-routes hypotheses via `hypothesis_id`; it could be rewritten on top of
`branch()` so each hypothesis is a genuine fork, and the report merges the
surviving findings (§39).