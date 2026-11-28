"""Demo-assistant agents — one public agent per capability.

The runtime schedules by artifact availability; `consumes` states each agent's
wake contract, while the agent functions hold the domain logic.
"""

from reactifact import Pipeline

from .calculate import calculate_aggregate
from .evaluate import ProgressEvaluator, build_answer
from .evidence import extract_evidence, verify_claims
from .planner import plan
from .search import resolve_table, resolve_text, search_sources

#: Router: greeting/direct → ChatReply, research → ResearchTurn.
Planner = plan

#: Fan-out over sources: refs → SourceRef + search-complete marker.
SearchScout = search_sources

#: Lazy materialization of a text link into a typed document.
Resolver = resolve_text

#: Lazy materialization of a structured ref into a spreadsheet (§29).
TableResolver = resolve_table

#: Extracts key facts from a document.
EvidenceBuilder = extract_evidence

#: Verifies facts: builds claims with confidence and finds contradictions.
Verifier = verify_claims

#: Computes over the spreadsheet if the question asks for an aggregate.
Calculator = calculate_aggregate

#: Assembles the answer once the turn becomes answerable (§17).
AnswerBuilder = build_answer

#: The full pipeline, in no particular order — the runtime schedules by
#: reaction to state changes, not by this list's order.
AGENTS = [
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
    "AnswerBuilder",
    "Calculator",
    "EvidenceBuilder",
    "PIPELINE",
    "Planner",
    "ProgressEvaluator",
    "Resolver",
    "SearchScout",
    "TableResolver",
    "Verifier",
]
