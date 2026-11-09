"""Demo-assistant agents — thin containers (§46, §48).

Logic lives in the Step classes (`examples/knowledge/steps/`); here we only
declare the steps contracts. Each step carries its own `consumes`/`produces`,
so the agent is just the runtime entry point.
"""

from reactifact import Agent

from .steps import (
    BuildAnswer,
    CalculateAggregate,
    EvaluateTurn,
    ExtractEvidence,
    PlannerReply,
    PlannerTurn,
    ResolveRef,
    ResolveTable,
    ScoutSources,
    VerifyClaims,
)


class Planner(Agent):
    """Router: greeting/direct → ChatReply, research → ResearchTurn."""

    name = "planner"
    steps = [PlannerReply(), PlannerTurn()]


class SearchScout(Agent):
    """Fan-out over sources: refs → SourceRef + search-complete marker."""

    name = "search_scout"
    steps = [ScoutSources()]


class ResolverAgent(Agent):
    """Lazy materialization of a text link into a typed document."""

    name = "resolver"
    steps = [ResolveRef()]


class TableResolver(Agent):
    """Lazy materialization of a structured ref into a spreadsheet (§29)."""

    name = "table_resolver"
    steps = [ResolveTable()]


class EvidenceBuilder(Agent):
    """Extracts key facts from a document."""

    name = "evidence_builder"
    steps = [ExtractEvidence()]


class VerifierAgent(Agent):
    """Verifies facts: builds claims with confidence and finds contradictions."""

    name = "verifier"
    steps = [VerifyClaims()]


class CalculatorAgent(Agent):
    """Computes over the spreadsheet if the question asks for an aggregate (§29, §67)."""

    name = "calculator"
    steps = [CalculateAggregate()]


class ProgressEvaluator(Agent):
    """Deterministic turn overseer: moves statuses forward (§24, §69)."""

    name = "progress_evaluator"
    steps = [EvaluateTurn()]
    priority = 100


class AnswerBuilder(Agent):
    """Assembles the answer once the turn becomes answerable (§17)."""

    name = "answer_builder"
    steps = [BuildAnswer()]


#: The full pipeline, in no particular order — the runtime schedules by
#: reaction to state changes, not by this list's order. Shared by chat.py
#: (console) and web.py (SSE) so the two transports can never drift apart.
AGENTS: list[Agent] = [
    Planner(),
    SearchScout(),
    ResolverAgent(),
    TableResolver(),
    EvidenceBuilder(),
    VerifierAgent(),
    CalculatorAgent(),
    ProgressEvaluator(),
    AnswerBuilder(),
]

__all__ = [
    "AGENTS",
    "AnswerBuilder",
    "CalculatorAgent",
    "EvidenceBuilder",
    "Planner",
    "ProgressEvaluator",
    "ResolverAgent",
    "SearchScout",
    "TableResolver",
    "VerifierAgent",
]
