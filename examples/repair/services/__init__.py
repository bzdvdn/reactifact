"""repair services — deterministic assistant logic (no LLM, §67).

Split by responsibility (the app imports via simple `services` names or the
specific submodules):

    facts     — required fields, human labels, pipeline stages
    fast      — canned replies for routine phrases
    geometry  — wall/floor/perimeter math and `ensure_geometry`
    catalog   — `price.csv` lexical search (no embedders)
    estimate  — quantity parsing → catalog pricing → totals
    rollback  — the "change → rebuild" stage routing (§22/§24)
"""

from __future__ import annotations

from .catalog import Catalog, CatalogItem
from .estimate import build_estimate, qa_budget_warning
from .facts import FACT_LABELS, REQUIRED_FACTS, STAGES, parse_facts
from .fast import (
    FAST_ABILITIES_TEXT,
    FAST_FAREWELL_TEXT,
    FAST_GREETING_TEXT,
    FAST_THANKS_TEXT,
    fast_reply,
)
from .geometry import Geometry, ensure_geometry, geometry_text
from .rollback import rollback_target

__all__ = [
    "Catalog",
    "CatalogItem",
    "FACT_LABELS",
    "FAST_ABILITIES_TEXT",
    "FAST_FAREWELL_TEXT",
    "FAST_GREETING_TEXT",
    "FAST_THANKS_TEXT",
    "Geometry",
    "REQUIRED_FACTS",
    "STAGES",
    "build_estimate",
    "ensure_geometry",
    "fast_reply",
    "geometry_text",
    "parse_facts",
    "qa_budget_warning",
    "rollback_target",
]
