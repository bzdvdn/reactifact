"""forklab agents — thin containers: one per fork-strategy, one for the merge."""

from __future__ import annotations

from reactifact import StepGroup

from .steps import BreadthInvestigate, DepthInvestigate, Evaluate


class StrategyGroup(StepGroup):
    """Runs in a *single* branch: spawn the Strategy first, this reacts."""

    name = "strategy"
    steps = [DepthInvestigate(), BreadthInvestigate()]


class EvaluatorGroup(StepGroup):
    """Runs on the merged context and produces the final Answer."""

    name = "evaluator"
    steps = [Evaluate()]
