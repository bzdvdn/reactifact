"""Index protocols, capability checks and deterministic point IDs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from ..errors import ConfigError
from ..models import Chunk, SearchHit, chunk_point_id, point_id


def check_dimensions(
    expected: int | None,
    vector: Sequence[float],
    *,
    backend: str,
) -> int:
    """Validate a vector's size against the backend's known dimensionality.

    Returns the vector's size. Raises `ConfigError` for an empty vector or a
    mismatch, so a wrong embedder fails at the boundary with a clear message
    instead of deep inside a database client.
    """
    size = len(vector)
    if size == 0:
        raise ConfigError(f"{backend} vector must not be empty")
    if expected is not None and size != expected:
        raise ConfigError(
            f"{backend} index is {expected}-dimensional, but a "
            f"{size}-dimensional vector arrived"
        )
    return size


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
        source_id: str | None = None,
    ) -> list[SearchHit]: ...


@runtime_checkable
class KeywordIndexer(Protocol):
    """Optional lexical search capability used by hybrid retrieval."""

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
        source_id: str | None = None,
    ) -> list[SearchHit]: ...


@runtime_checkable
class BatchIndexer(Protocol):
    """Optional bulk upsert capability."""

    async def upsert_batch(
        self,
        items: Sequence[tuple[Chunk, list[float], str]],
        *,
        source_id: str,
    ) -> None: ...


@runtime_checkable
class CountableIndex(Protocol):
    """Optional point-count capability for status pages and health checks."""

    async def count(self) -> int: ...


__all__ = [
    "BatchIndexer",
    "CountableIndex",
    "Indexer",
    "KeywordIndexer",
    "check_dimensions",
    "chunk_point_id",
    "point_id",
]
