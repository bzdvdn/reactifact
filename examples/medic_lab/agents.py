"""medic-lab agents — thin containers, example-local (§48)."""

from reactifact import Agent, Consume, Produce
from reactifact.interrupt import PendingQuestion
from reactifact.scheduler import Scheduler, relation_balance_metric, uncertainty_policy
from reactifact.sources import SourceRef

from .models import (
    Claim,
    Evidence,
    Hypothesis,
    Question,
    SearchDone,
    TypedDoc,
)
from .produce import (
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
    consumes = [Consume(Question)]
    produces = [Generator()]


class InvestigatorAgent(Agent):
    name = "investigator"
    consumes = [Consume.by_status(Hypothesis, "open")]
    produces = [Investigator(), Produce(SearchDone)]


class ResolverAgent(Agent):
    name = "resolver"
    consumes = [Consume(SourceRef)]
    produces = [Resolver()]


class EvidenceAgent(Agent):
    name = "evidence_extractor"
    concurrency_limit = 2  # LLM-bound producers (§66)
    consumes = [Consume(TypedDoc)]
    produces = [ExtractEvidence()]


class ClaimsAgent(Agent):
    name = "claim_builder"
    consumes = [Consume(Evidence)]
    produces = [ClaimBuilder()]


class CrossCheckAgent(Agent):
    name = "cross_checker"
    consumes = [Consume(Claim)]
    produces = [CrossChecker()]
    priority = 50


class EvaluatorAgent(Agent):
    name = "evaluator"
    consumes = [
        Consume(Hypothesis),
        Consume(Evidence),
        Consume(Claim),
        Consume(TypedDoc),
        Consume(SearchDone),
    ]
    produces = [Evaluator()]
    priority = 100


class SteerAgent(Agent):
    name = "steer"
    consumes = [Consume(Hypothesis), Consume(Claim), Consume(Evidence)]
    produces = [Steer(), Produce(PendingQuestion)]
    priority = 100


class DeepenAgent(Agent):
    name = "deepen"
    concurrency_limit = 2  # LLM-bound producers (§66)
    consumes = [Consume.by_field(PendingQuestion, "answered", True)]
    produces = [Deepen()]


class ReporterAgent(Agent):
    name = "reporter"
    concurrency_limit = 2  # LLM-bound producers (§66)
    consumes = [
        Consume(Hypothesis),
        Consume(PendingQuestion),
        Consume(Question),
    ]
    produces = [Reporter()]
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
    `Evaluator` already scores by hand (`produce/evaluate.py`) — this reuses
    the signal to drive execution order, not just the report."""
    return uncertainty_policy(metric=relation_balance_metric())
