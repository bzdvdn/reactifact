"""medic-lab agents — thin containers, example-local (§48)."""

from reactifact import Agent, Pipeline
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

#: LLM-bound producers run at most two at a time (§66).
GeneratorAgent = Agent("generator", steps=[Generator()], concurrency_limit=2)

InvestigatorAgent = Agent("investigator", steps=[Investigator()])

ResolverAgent = Agent("resolver", steps=[Resolver()])

EvidenceAgent = Agent(
    "evidence_extractor",
    steps=[ExtractEvidence()],
    concurrency_limit=2,  # LLM-bound producers (§66)
)

ClaimsAgent = Agent("claim_builder", steps=[ClaimBuilder()])

CrossCheckAgent = Agent("cross_checker", steps=[CrossChecker()], priority=50)

EvaluatorAgent = Agent("evaluator", steps=[Evaluator()], priority=100)

SteerAgent = Agent("steer", steps=[Steer()], priority=100)

DeepenAgent = Agent(
    "deepen",
    steps=[Deepen()],
    concurrency_limit=2,  # LLM-bound producers (§66)
)

ReporterAgent = Agent(
    "reporter",
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
    `Evaluator` already scores by hand (`steps/evaluate.py`) — this reuses
    the signal to drive execution order, not just the report."""
    return uncertainty_policy(metric=relation_balance_metric())
