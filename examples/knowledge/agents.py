"""Demo-assistant agents — thin containers (§46, §48).

Logic lives in the Step classes (`examples/knowledge/steps/`); here we only
declare the steps contracts. Each step carries its own `reads`/`produces`,
so the agent is just the runtime entry point. `PIPELINE` is the shared
definition behind both transports.
"""

from reactifact import Agent, Pipeline

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

#: Router: greeting/direct → ChatReply, research → ResearchTurn.
Planner = Agent("planner", steps=[PlannerReply(), PlannerTurn()])

#: Fan-out over sources: refs → SourceRef + search-complete marker.
SearchScout = Agent("search_scout", steps=[ScoutSources()])

#: Lazy materialization of a text link into a typed document.
Resolver = Agent("resolver", steps=[ResolveRef()])

#: Lazy materialization of a structured ref into a spreadsheet (§29).
TableResolver = Agent("table_resolver", steps=[ResolveTable()])

#: Extracts key facts from a document.
EvidenceBuilder = Agent("evidence_builder", steps=[ExtractEvidence()])

#: Verifies facts: builds claims with confidence and finds contradictions.
Verifier = Agent("verifier", steps=[VerifyClaims()])

#: Computes over the spreadsheet if the question asks for an aggregate
#: (§29, §67).
Calculator = Agent("calculator", steps=[CalculateAggregate()])

#: Deterministic turn overseer: moves statuses forward (§24, §69).
ProgressEvaluator = Agent(
    "progress_evaluator",
    steps=[EvaluateTurn()],
    priority=100,
)

#: Assembles the answer once the turn becomes answerable (§17).
AnswerBuilder = Agent("answer_builder", steps=[BuildAnswer()])

#: The full pipeline, in no particular order — the runtime schedules by
#: reaction to state changes, not by this list's order. Shared by chat.py
#: (console) and web.py (SSE) so the two transports can never drift apart.
AGENTS: list[Agent] = [
    Planner,
    SearchScout,
    Resolver,
    TableResolver,
    EvidenceBuilder,
    Verifier,
    Calculator,
    ProgressEvaluator,
    AnswerBuilder,
]

#: The reusable definition: `Chat(pipeline=PIPELINE)` or `PIPELINE.agents`.
PIPELINE = Pipeline(agents=AGENTS, name="knowledge")


__all__ = [
    "AGENTS",
    "PIPELINE",
    "AnswerBuilder",
    "Calculator",
    "EvidenceBuilder",
    "Planner",
    "ProgressEvaluator",
    "Resolver",
    "SearchScout",
    "TableResolver",
    "Verifier",
]
