# repair — budget-aware replanning demo

A room-repair assistant: it gathers the project facts (room type, area, budget),
optionally asks you for design decisions, builds a plan and a material estimate
from the local price catalog, and — when you say the plan is over budget —
**replans** from the plan stage instead of just trimming the estimate.

> **Note:** the chat and its demo data (catalog, plan, palettes) are intentionally
> in **Russian**. The code and comments are in English; the runtime/prompting text
> stays Russian because the demo targets a Russian repair workflow. Example:
> `«сделай ремонт ванной»`.

## Run

```bash
.venv/bin/python examples/repair/chat.py     # console, no API keys needed
.venv/bin/python examples/repair/web.py      # FastAPI + SSE web UI on :8000
```

![Five stage agents react to Project.stage; going over budget sends the flow back to plan, not to a shrunk total.](../../docs/img/repair-flow.svg)

## Highlights demonstrated

- `Context`-driven state machine via `Project.stage` (collect → design → approval),
  no explicit graph.
- Human-in-the-loop: `PendingQuestion` for approvals and design picks; humans
  answer in natural language, not buttons.
- Deterministic estimate from `data/price.csv` (lexical match, no LLM, §67) with
  budget-aware replan: going over budget rebuilds the *plan*, not just the total.
- Honest fallbacks: without an LLM the demo still runs — canned replies,
  fallback design options, and clear "can't decide honestly" messages (§59, §68).
- Sessions persist to SQLite via `AppendBackend`: the first save writes a
  snapshot, each later turn appends only its commits (plus the pending-event
  queue), so history stays replayable without rewriting the whole `Context`.

## Data

`data/price.csv` is a real-weight construction catalog (bricks, plywood,
drywall, blocks) in ₽; `data/materials.csv` is a small curated list for the
fast catalog demo. Both are Russian by design (see the note above).

## Scenarios

`scenarios/` holds `reactifact.testing.ScenarioLab` scenarios — a separate track
from the unit tests in `tests/`, run through the `reactifact scenario` CLI so a
plain `pytest` run never needs a model key or a network connection:

```bash
.venv/bin/python -m reactifact scenario examples.repair.scenarios
.venv/bin/python -m reactifact scenario examples.repair.scenarios --mode record   # real OpenRouter call
.venv/bin/python -m reactifact scenario examples.repair.scenarios --mode replay   # offline, from the fixture
```

## Structure

```
examples/repair/
  models.py            Project/ProjectInfo/PlanStep/Estimate/ChatReply/…
  prompts.py           role prompts (PromptTemplate)
  produce/             the staged pipeline
    common.py          shared helpers + the reset map (change → rebuild)
    design.py          LLM design options + preview rendering
    plan.py            the plan prompt and driver
    stages.py          six stages (collect → … → assistant)
  services/            deterministic logic (no LLM, §67)
    facts.py · fast.py · geometry.py · catalog.py · estimate.py · rollback.py
  fallbacks.py         demo-mode fallbacks (no model)
  image_prompt.py      photo-preview prompt builder
  agents.py            RepairFlow (thin container over the stages)
  chat.py · web.py     entrypoints (console · FastAPI/SSE)
  scenarios/           ScenarioLab scenarios (see above; recorded fixtures in scenarios/data/)
  data/ · web/ · sessions/
```