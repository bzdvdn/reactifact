"""The flagship demo question ("how much does gpu cost in total?") run with
`llm=None`: search, table resolution and the sum aggregation are all
deterministic (§67), so the exact total and its provenance can be asserted
without a model in the loop at all — proving the number in the answer came
from `gpu_usage.csv`, not a guess."""

from __future__ import annotations

from reactifact.testing import ScenarioLab, scenario

from ..agents import AGENTS
from ..models import Answer, Calculation, UserQuery
from ._common import GPU_COST_QUESTION, resources


@scenario("knowledge: gpu cost answer is computed, not guessed")
async def gpu_cost_answer_is_computed_not_guessed() -> None:
    lab = ScenarioLab(list(AGENTS), resources=resources)

    result = await lab.run(UserQuery(text=GPU_COST_QUESTION))

    calc = result.artifacts(Calculation).exists()
    assert calc.value == 3580  # sum of the "gpu cost usd" column in gpu_usage.csv
    assert calc.column == "gpu cost usd"
    answer = result.artifacts(Answer).exists()
    assert "3580" in answer.text
    assert "costs:gpu_usage.csv" in answer.sources
    result.llm.max_calls(0)  # search/aggregation/fallback text — no model needed
    result.errors.none()
