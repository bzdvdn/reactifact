"""Per-turn budget, deadline and LLM usage accounting."""

from __future__ import annotations

import time

from ..budget import Budget, BudgetLLM, BudgetTracker, RunOutcome
from ..logging import get_logger
from ..quota import QuotaLLM
from ..resources import RuntimeResources

logger = get_logger("reactifact.runtime")


class TurnBudget:
    """The active budget, deadline, counters and LLM wrappers for one turn."""

    def __init__(self) -> None:
        self.active: Budget | None = None
        self.deadline: float | None = None
        self.started_at = 0.0
        self.runs_used = 0
        self.errors_used = 0
        self.tracker: BudgetTracker | None = None
        self.quota_exceeded = False
        self.original_llm: object | None = None

    def begin(self, budget: Budget | None, resources: RuntimeResources) -> None:
        self.active = budget
        self.runs_used = 0
        self.errors_used = 0
        self.deadline = None
        self.started_at = time.monotonic()
        self.tracker = None
        self.original_llm = None
        self.quota_exceeded = False
        if self.active is not None and self.active.max_seconds is not None:
            self.deadline = self.started_at + self.active.max_seconds

        resources.budget = self.active
        resources.budget_deadline = self.deadline

        if (
            self.active is not None
            and self.active.max_cost is not None
            and resources.pricer is None
        ):
            logger.warning(
                "Budget.max_cost is set but RuntimeResources.pricer is None; "
                "cost is not tracked and the limit is inert."
            )
        if (
            self.active is not None
            and self.active.tracks_llm_usage
            and resources.llm is not None
        ):
            self.tracker = BudgetTracker()
            self.original_llm = resources.llm
            resources.llm = BudgetLLM(
                self.original_llm,
                self.tracker,
                pricer=resources.pricer,
            )
        resources.budget_tracker = self.tracker

        quota = resources.quota
        if quota is not None:
            if quota.exceeded(resources.quota_key()) is not None:
                self.quota_exceeded = True
            if resources.llm is not None:
                if self.original_llm is None:
                    self.original_llm = resources.llm
                resources.llm = QuotaLLM(
                    resources.llm,
                    quota,
                    key=resources.quota_key(),
                    pricer=resources.pricer,
                )

    def finish(self, resources: RuntimeResources) -> None:
        if self.original_llm is not None:
            resources.llm = self.original_llm  # type: ignore[assignment]
            self.original_llm = None

    def count_run(self) -> None:
        self.runs_used += 1

    def count_error(self) -> None:
        self.errors_used += 1

    def exhausted(self) -> RunOutcome | None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            return RunOutcome.BUDGET_TIME_EXCEEDED
        if self.quota_exceeded:
            return RunOutcome.QUOTA_EXCEEDED
        if (
            self.active is not None
            and self.active.max_runs is not None
            and self.runs_used >= self.active.max_runs
        ):
            return RunOutcome.BUDGET_RUNS_EXCEEDED
        if self.tracker is not None:
            return self.tracker.exhausted(self.active)
        return None

    def remaining_seconds(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())


__all__ = ["TurnBudget"]
