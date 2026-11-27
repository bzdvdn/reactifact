"""A single deterministic stage, seeded straight past collect/pick: the
estimate stage prices a plan from the local catalog, no LLM involved (§67)."""

from __future__ import annotations

from reactifact.testing import ScenarioLab, scenario

from ..agents import RepairFlow
from ..models import Estimate, PlanStep, Project
from ._common import resources


@scenario("repair: estimate stage prices the plan from the catalog")
async def estimate_prices_from_catalog() -> None:
    lab = ScenarioLab([RepairFlow], resources=resources)

    plan = [
        PlanStep(
            name="Демонтаж",
            description="Снять старое покрытие стен",
            materials=["Грунтовка глубокого проникновения ~5 л"],
        ),
    ]
    result = await lab.run(Project(stage="estimate", plan=plan))

    project = result.artifacts(Project).exists()
    assert project.stage == "final_approval"
    estimate = project.estimate
    assert isinstance(estimate, Estimate)
    assert estimate.lines  # priced at least one material from price.csv
    result.llm.max_calls(0)  # pricing is catalog lookup, never the model (§67)
