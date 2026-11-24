"""The provenance relation vocabulary.

Relation names are strings on the wire, but code should import them rather
than spell them: a typo in a string silently creates an edge nothing queries.
`RelationName` is a `StrEnum`, so it compares and serializes as a plain string and
custom app-specific names remain allowed (`RelationName | str` everywhere).
"""

from __future__ import annotations

from enum import StrEnum


class RelationName(StrEnum):
    """Known provenance edge names (custom strings are still accepted)."""

    SUPPORTED_BY = "supported_by"
    DERIVED_FROM = "derived_from"
    EXTRACTED_FROM = "extracted_from"
    MATERIALIZED_FROM = "materialized_from"
    RESOLVED_FROM = "resolved_from"
    CALCULATED_FROM = "calculated_from"
    REFUTED_BY = "refuted_by"
    CONTRADICTED_BY = "contradicted_by"


SUPPORTED_BY = RelationName.SUPPORTED_BY
DERIVED_FROM = RelationName.DERIVED_FROM
EXTRACTED_FROM = RelationName.EXTRACTED_FROM
MATERIALIZED_FROM = RelationName.MATERIALIZED_FROM
RESOLVED_FROM = RelationName.RESOLVED_FROM
CALCULATED_FROM = RelationName.CALCULATED_FROM
REFUTED_BY = RelationName.REFUTED_BY
CONTRADICTED_BY = RelationName.CONTRADICTED_BY

#: Relations `audit.build_report` follows when no explicit list is passed.
DEFAULT_RELATIONS: tuple[str, ...] = tuple(relation.value for relation in RelationName)

__all__ = [
    "CALCULATED_FROM",
    "CONTRADICTED_BY",
    "DEFAULT_RELATIONS",
    "DERIVED_FROM",
    "EXTRACTED_FROM",
    "MATERIALIZED_FROM",
    "REFUTED_BY",
    "RESOLVED_FROM",
    "RelationName",
    "SUPPORTED_BY",
]
