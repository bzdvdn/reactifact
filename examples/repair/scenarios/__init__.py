"""ScenarioLab scenarios for the repair assistant — run with:

    reactifact scenario examples.repair.scenarios
    reactifact scenario examples.repair.scenarios -k "estimate"
    reactifact scenario examples.repair.scenarios --mode record   # hits OpenRouter for real
    reactifact scenario examples.repair.scenarios --mode replay   # offline, from the fixture

These do **not** run under `pytest` — scenarios are a separate track from the
unit tests in `tests/`, run through the `reactifact scenario` CLI, so a plain
`pytest` run never needs a model key or a network connection (see
`reactifact.testing`).

Four scenarios over the staged pipeline (`collect -> design_choice -> plan ->
estimate -> final_approval -> assistant`, see
`examples/repair/steps/stages.py`), one per module:

- `greeting` — the fast-reply table answers a greeting without touching
  the LLM at all.
- `estimate` — the estimate stage is unit tested in isolation (seeded
  straight into `stage="estimate"`), deterministic catalog pricing, no LLM.
- `fallback` — the honest-fallback path (§59): with the LLM forced to fail,
  the assistant stage still answers from the approved plan instead of
  crashing or stalling.
- `collect_live` — record once against the real OpenRouter model
  (`--mode record`), then replay that exact recording forever after
  (`--mode replay`), fully offline — proving the real model's structured
  output still parses, without needing a key in CI. With no `--mode` flag
  it skips itself rather than guessing.
- `full_flow` — a multi-turn scenario (`lab.scenario()`/`.turn()`) walking
  the whole staged pipeline in one conversation: description -> design pick
  -> plan + estimate -> approval, asserting `Project.stage` after each turn.

`_common.py` holds the shared resources/fixture-path/stub-LLM helpers.
"""

from __future__ import annotations

from . import collect_live, estimate, fallback, full_flow, greeting

__all__ = ["collect_live", "estimate", "fallback", "full_flow", "greeting"]
