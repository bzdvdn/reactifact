"""Index protocols and deterministic point IDs."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Protocol

from ..models import Chunk, SearchHit


def point_id(source_uri: str, chunk_text: str) -> uuid.UUID:
    """Deterministic point ID so identical content replaces, not duplicates."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{source_uri}\n{chunk_text}")


class Indexer(Protocol):
    """Persists chunk vectors with idempotent upserts and reconciliation."""

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None: ...

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None: ...

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]: ...


class KeywordIndexer(Protocol):
    """Optional lexical search capability used by hybrid retrieval."""

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]: ...


class BatchIndexer(Protocol):
    """Optional bulk upsert capability."""

    async def upsert_batch(
        self,
        items: Sequence[tuple[Chunk, list[float], str]],
        *,
        source_id: str,
    ) -> None: ...


__all__ = ["BatchIndexer", "Indexer", "KeywordIndexer", "point_id"]
