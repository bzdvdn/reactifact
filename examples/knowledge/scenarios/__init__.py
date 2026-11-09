"""ScenarioLab scenarios for the knowledge chat — run with:

    reactifact scenario examples.knowledge.scenarios
    reactifact scenario examples.knowledge.scenarios -k "greeting"
    reactifact scenario examples.knowledge.scenarios --mode record   # hits the real model
    reactifact scenario examples.knowledge.scenarios --mode replay   # offline, from the fixture

These do **not** run under `pytest` — scenarios are a separate track from the
unit tests in `tests/`, run through the `reactifact scenario` CLI, so a plain
`pytest` run never needs a model key or a network connection (see
`reactifact.testing`).

Three scenarios over the multi-source pipeline (`search -> evidence -> claim
verification -> calc -> answer`, see `examples/knowledge/steps/`), one per
module:

- `greeting` — the router answers a greeting from its regex table without
  touching the LLM.
- `gpu_cost` — the flagship demo question ("how much does gpu cost in
  total?") run with `llm=None`: search, table resolution and the sum
  aggregation are all deterministic (§67), so the exact total and its
  provenance can be asserted without a model in the loop at all — proving
  the number in the answer came from `gpu_usage.csv`, not a guess.
- `research_live` — record once against the real model (`--mode record`),
  then replay that exact recording forever after (`--mode replay`), fully
  offline — proving the real model actually assembles a coherent, sourced
  answer from the retrieved evidence, without needing a key in CI.
- `memory` — a multi-turn scenario (`lab.scenario()`/`.turn()`) proving chat
  memory (`steps/common.py`'s `conversation_text`) still holds the first
  question and its answer by the time a follow-up question is asked.

`_common.py` holds the shared resources/fixture-path/stub-LLM helpers.
"""

from __future__ import annotations

from . import gpu_cost, greeting, memory, research_live

__all__ = ["gpu_cost", "greeting", "memory", "research_live"]
