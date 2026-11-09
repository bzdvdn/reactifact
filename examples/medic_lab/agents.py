"""medic-lab agents — thin containers, example-local (§48)."""

from reactifact import Agent
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


class GeneratorAgent(Agent):
    name = "generator"
    concurrency_limit = 2  # LLM-bound producers (§66)
    steps = [Generator()]


class InvestigatorAgent(Agent):
    name = "investigator"
    steps = [Investigator()]


class ResolverAgent(Agent):
    name = "resolver"
    steps = [Resolver()]


class EvidenceAgent(Agent):
    name = "evidence_extractor"
    concurrency_limit = 2  # LLM-bound producers (§66)
    steps = [ExtractEvidence()]


class ClaimsAgent(Agent):
    name = "claim_builder"
    steps = [ClaimBuilder()]


class CrossCheckAgent(Agent):
    name = "cross_checker"
    steps = [CrossChecker()]
    priority = 50


class EvaluatorAgent(Agent):
    name = "evaluator"
    steps = [Evaluator()]
    priority = 100


class SteerAgent(Agent):
    name = "steer"
    steps = [Steer()]
    priority = 100


class DeepenAgent(Agent):
    name = "deepen"
    concurrency_limit = 2  # LLM-bound producers (§66)
    steps = [Deepen()]


class ReporterAgent(Agent):
    name = "reporter"
    concurrency_limit = 2  # LLM-bound producers (§66)
    steps = [Reporter()]
    priority = 100


def medic_lab_agents() -> list[Agent]:
    return [
        GeneratorAgent(),
        InvestigatorAgent(),
        ResolverAgent(),
        EvidenceAgent(),
        ClaimsAgent(),
        CrossCheckAgent(),
        EvaluatorAgent(),
        SteerAgent(),
        DeepenAgent(),
        ReporterAgent(),
    ]


def medic_lab_scheduler() -> Scheduler:
    """Uncertainty-driven scheduling (§26): when several open hypotheses are
    eligible for (re-)investigation in the same generation, prioritize the
    most-contradicted one first. Same `supports`/`contradicts` relations
    `Evaluator` already scores by hand (`steps/evaluate.py`) — this reuses
    the signal to drive execution order, not just the report."""
    return uncertainty_policy(metric=relation_balance_metric())
