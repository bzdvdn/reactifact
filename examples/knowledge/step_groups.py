"""Demo-assistant agents — thin containers (§46, §48).

Logic lives in the Step classes (`examples/knowledge/steps/`); here we only
declare the steps contracts. Each step carries its own `consumes`/`produces`,
so the agent is just the runtime entry point.
"""

from reactifact import StepGroup

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


class Planner(StepGroup):
    """Router: greeting/direct → ChatReply, research → ResearchTurn."""

    name = "planner"
    steps = [PlannerReply(), PlannerTurn()]


class SearchScout(StepGroup):
    """Fan-out over sources: refs → SourceRef + search-complete marker."""

    name = "search_scout"
    steps = [ScoutSources()]


class ResolverGroup(StepGroup):
    """Lazy materialization of a text link into a typed document."""

    name = "resolver"
    steps = [ResolveRef()]


class TableResolver(StepGroup):
    """Lazy materialization of a structured ref into a spreadsheet (§29)."""

    name = "table_resolver"
    steps = [ResolveTable()]


class EvidenceBuilder(StepGroup):
    """Extracts key facts from a document."""

    name = "evidence_builder"
    steps = [ExtractEvidence()]


class VerifierGroup(StepGroup):
    """Verifies facts: builds claims with confidence and finds contradictions."""

    name = "verifier"
    steps = [VerifyClaims()]


class CalculatorGroup(StepGroup):
    """Computes over the spreadsheet if the question asks for an aggregate (§29, §67)."""

    name = "calculator"
    steps = [CalculateAggregate()]


class ProgressEvaluator(StepGroup):
    """Deterministic turn overseer: moves statuses forward (§24, §69)."""

    name = "progress_evaluator"
    steps = [EvaluateTurn()]
    priority = 100


class AnswerBuilder(StepGroup):
    """Assembles the answer once the turn becomes answerable (§17)."""

    name = "answer_builder"
    steps = [BuildAnswer()]


#: The full pipeline, in no particular order — the runtime schedules by
#: reaction to state changes, not by this list's order. Shared by chat.py
#: (console) and web.py (SSE) so the two transports can never drift apart.
GROUPS: list[StepGroup] = [
    Planner(),
    SearchScout(),
    ResolverGroup(),
    TableResolver(),
    EvidenceBuilder(),
    VerifierGroup(),
    CalculatorGroup(),
    ProgressEvaluator(),
    AnswerBuilder(),
]

__all__ = [
    "GROUPS",
    "AnswerBuilder",
    "CalculatorGroup",
    "EvidenceBuilder",
    "Planner",
    "ProgressEvaluator",
    "ResolverGroup",
    "SearchScout",
    "TableResolver",
    "VerifierGroup",
]
