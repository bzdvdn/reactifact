"""medic-lab agents — thin containers, example-local (§48)."""

from reactifact import StepGroup
from reactifact.scheduler import Scheduler, relation_balance_metric, uncertainty_policy

from .steps import (
    ClaimBuilder,
    CrossChecker,
    Deepen,
    Evaluator,
    ExtractEvidence,
    Generator,
    Investigator,
    Reporter,
    Resolver,
    Steer,
)


class GeneratorGroup(StepGroup):
    name = "generator"
    concurrency_limit = 2  # LLM-bound producers (§66)
    steps = [Generator()]


class InvestigatorGroup(StepGroup):
    name = "investigator"
    steps = [Investigator()]


class ResolverGroup(StepGroup):
    name = "resolver"
    steps = [Resolver()]


class EvidenceGroup(StepGroup):
    name = "evidence_extractor"
    concurrency_limit = 2  # LLM-bound producers (§66)
    steps = [ExtractEvidence()]


class ClaimsGroup(StepGroup):
    name = "claim_builder"
    steps = [ClaimBuilder()]


class CrossCheckGroup(StepGroup):
    name = "cross_checker"
    steps = [CrossChecker()]
    priority = 50


class EvaluatorGroup(StepGroup):
    name = "evaluator"
    steps = [Evaluator()]
    priority = 100


class SteerGroup(StepGroup):
    name = "steer"
    steps = [Steer()]
    priority = 100


class DeepenGroup(StepGroup):
    name = "deepen"
    concurrency_limit = 2  # LLM-bound producers (§66)
    steps = [Deepen()]


class ReporterGroup(StepGroup):
    name = "reporter"
    concurrency_limit = 2  # LLM-bound producers (§66)
    steps = [Reporter()]
    priority = 100


def medic_lab_groups() -> list[StepGroup]:
    return [
        GeneratorGroup(),
        InvestigatorGroup(),
        ResolverGroup(),
        EvidenceGroup(),
        ClaimsGroup(),
        CrossCheckGroup(),
        EvaluatorGroup(),
        SteerGroup(),
        DeepenGroup(),
        ReporterGroup(),
    ]


def medic_lab_scheduler() -> Scheduler:
    """Uncertainty-driven scheduling (§26): when several open hypotheses are
    eligible for (re-)investigation in the same generation, prioritize the
    most-contradicted one first. Same `supports`/`contradicts` relations
    `Evaluator` already scores by hand (`steps/evaluate.py`) — this reuses
    the signal to drive execution order, not just the report."""
    return uncertainty_policy(metric=relation_balance_metric())
