"""medic-lab agents — thin containers, example-local (§48)."""

from reactifact import Agent, Consume, PendingQuestion, Pipeline
from reactifact.scheduler import Scheduler, relation_balance_metric, uncertainty_policy
from reactifact.sources import SourceRef

from ..models import (
    Claim,
    Evidence,
    Hypothesis,
    Question,
    SearchDone,
    TypedDoc,
)
from ._evaluate import evaluate
from ._stages import (
    ClaimBuilder,
    CrossChecker,
    ExtractEvidence,
    Generator,
    Investigator,
    Resolver,
)
from ._steer import Deepen, Reporter, Steer

#: LLM-bound producers run at most two at a time (§66).
GeneratorAgent = Agent(
    "generator",
    consumes=[Consume(Question)],
    steps=[Generator()],
    concurrency_limit=2,
)

InvestigatorAgent = Agent(
    "investigator",
    consumes=[Consume.by_field(Hypothesis, "status", "open")],
    steps=[Investigator()],
)

ResolverAgent = Agent(
    "resolver",
    consumes=[Consume(SourceRef)],
    steps=[Resolver()],
)

EvidenceAgent = Agent(
    "evidence_extractor",
    consumes=[Consume(TypedDoc)],
    steps=[ExtractEvidence()],
    concurrency_limit=2,  # LLM-bound producers (§66)
)

ClaimsAgent = Agent(
    "claim_builder",
    consumes=[Consume(Evidence)],
    steps=[ClaimBuilder()],
)

CrossCheckAgent = Agent(
    "cross_checker",
    consumes=[Consume(Claim)],
    steps=[CrossChecker()],
    priority=50,
)

EvaluatorAgent = Agent(
    "evaluator",
    consumes=[
        Consume(Hypothesis),
        Consume(Evidence),
        Consume(Claim),
        Consume(TypedDoc),
        Consume(SearchDone),
    ],
    steps=[evaluate],
    priority=100,
)

SteerAgent = Agent(
    "steer",
    consumes=[Consume(Hypothesis), Consume(Claim), Consume(Evidence)],
    steps=[Steer()],
    priority=100,
)

DeepenAgent = Agent(
    "deepen",
    consumes=[Consume.by_field(PendingQuestion, "answered", True)],
    steps=[Deepen()],
    concurrency_limit=2,  # LLM-bound producers (§66)
)

ReporterAgent = Agent(
    "reporter",
    consumes=[Consume(Hypothesis), Consume(PendingQuestion)],
    steps=[Reporter()],
    concurrency_limit=2,  # LLM-bound producers (§66)
    priority=100,
)


def medic_lab_agents() -> list[Agent]:
    return [
        GeneratorAgent,
        InvestigatorAgent,
        ResolverAgent,
        EvidenceAgent,
        ClaimsAgent,
        CrossCheckAgent,
        EvaluatorAgent,
        SteerAgent,
        DeepenAgent,
        ReporterAgent,
    ]


#: The reusable definition behind the console and the web app.
PIPELINE = Pipeline(agents=medic_lab_agents(), name="medic-lab")


def medic_lab_scheduler() -> Scheduler:
    """Uncertainty-driven scheduling (§26): when several open hypotheses are
    eligible for (re-)investigation in the same generation, prioritize the
    most-contradicted one first. Same `supports`/`contradicts` relations
    `Evaluator` already scores by hand (`agents/evaluate.py`) — this reuses
    the signal to drive execution order, not just the report."""
    return uncertainty_policy(metric=relation_balance_metric())
