"""knowledge produce — routing, search, evidence, calculation, lifecycle.

Module layout mirrors the pipeline stage by stage (§48):
`router` (greeting vs research) → `search` (fan-out + text/table materialization)
→ `evidence` (extraction + claim verification) → `calc` (structured-data
aggregation) → `lifecycle` (turn overseer + answer builder). `common` holds the
shared deterministic helpers. The package re-exports every Step class so the
agent containers keep a flat import surface.
"""

from .calc import CalculateAggregate
from .evidence import ExtractEvidence, VerifyClaims
from .lifecycle import BuildAnswer, EvaluateTurn
from .router import PlannerReply, PlannerTurn
from .search import ResolveRef, ResolveTable, ScoutSources

__all__ = [
    "BuildAnswer",
    "CalculateAggregate",
    "EvaluateTurn",
    "ExtractEvidence",
    "PlannerReply",
    "PlannerTurn",
    "ResolveRef",
    "ResolveTable",
    "ScoutSources",
    "VerifyClaims",
]
