"""estimate stage — deterministic catalog pricing (no LLM) → approval."""

from __future__ import annotations

from typing import Any

from reactifact import Artifact, Edit, Progress, Step

from ...models import Project
from ...services.catalog import Catalog
from ...services.estimate import build_estimate, qa_budget_warning


class EstimateStage(Step):
    """Estimate — deterministically, via the catalog (no LLM) → final_approval."""

    consumes = (Project,)
    produces = (Project,)

    async def run(
        self,
        project: Artifact[Project],
        catalog: Catalog,
        progress: Progress,
    ) -> Any:
        if project.data.stage != "estimate":
            return None
        if project.data.estimate is not None:
            return None
        progress("Считаю смету…")
        estimate = build_estimate(project.data.plan, catalog)
        estimate.warnings.extend(qa_budget_warning(estimate, project.data.info.budget))
        return Edit(project, estimate=estimate, stage="final_approval")


__all__ = ["EstimateStage"]
