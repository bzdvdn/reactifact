"""In-memory index — for tests, dry-runs, and tiny datasets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..models import Chunk, SearchHit
from .acl import acl_visible
from .base import point_id
from .keyword import bm25_scores, tokenize
from .math import clamp, cosine_similarity


def _matches(metadata: Mapping[str, object], filters: Mapping[str, str] | None) -> bool:
    if not filters:
        return True
    return all(str(metadata.get(key)) == value for key, value in filters.items())


@dataclass
class _Point:
    source_id: str
    artifact_hash: str
    chunk: Chunk
    vector: list[float]


@dataclass
class MemoryIndex:
    """Stores `(chunk, vector)` pairs in memory for the process lifetime."""

    points: dict[str, _Point] = field(default_factory=dict)

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
        pid = str(point_id(chunk.source_uri, chunk.text))
        self.points[pid] = _Point(
            source_id=source_id,
            artifact_hash=artifact_hash,
            chunk=chunk,
            vector=vector,
        )

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        stale = [
            pid
            for pid, point in self.points.items()
            if point.source_id == source_id
            and (
                point.chunk.source_uri not in current
                or current[point.chunk.source_uri] != point.artifact_hash
            )
        ]
        for pid in stale:
            del self.points[pid]

    @property
    def count(self) -> int:
        return len(self.points)

    def entries(self) -> list[tuple[Chunk, list[float]]]:
        return [(point.chunk, point.vector) for point in self.points.values()]

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        points = [
            point
            for point in self.points.values()
            if _matches(point.chunk.metadata, filters)
            and acl_visible(point.chunk.metadata, principals)
        ]
        points.sort(
            key=lambda point: cosine_similarity(vector, point.vector),
            reverse=True,
        )
        return [
            SearchHit(
                text=point.chunk.text,
                source_uri=point.chunk.source_uri,
                metadata=dict(point.chunk.metadata),
                score=clamp(cosine_similarity(vector, point.vector)),
            )
            for point in points[:top_k]
        ]

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        points = [
            point
            for point in self.points.values()
            if _matches(point.chunk.metadata, filters)
            and acl_visible(point.chunk.metadata, principals)
        ]
        query_tokens = tokenize(text)
        scores = bm25_scores(
            query_tokens, [tokenize(point.chunk.text) for point in points]
        )
        ranked = sorted(
            zip(points, scores, strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
        return [
            SearchHit(
                text=point.chunk.text,
                source_uri=point.chunk.source_uri,
                metadata=dict(point.chunk.metadata),
                score=score,
            )
            for point, score in ranked[:top_k]
        ]


__all__ = ["MemoryIndex"]
