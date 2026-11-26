"""Reciprocal Rank Fusion for hybrid retrieval."""

from __future__ import annotations

from collections.abc import Sequence

from ..models import SearchHit

_DEFAULT_K = 60


def _identity(hit: SearchHit) -> tuple[str, str]:
    return (hit.source_uri, hit.text)


def reciprocal_rank_fusion(
    *rankings: Sequence[SearchHit],
    k: int = _DEFAULT_K,
) -> list[SearchHit]:
    """Merge ranked hit lists into one, best-first."""
    fused: dict[tuple[str, str], float] = {}
    representative: dict[tuple[str, str], SearchHit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            key = _identity(hit)
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
            previous = representative.get(key)
            if previous is None:
                representative[key] = hit
                continue
            representative[key] = previous.model_copy(
                update={
                    "dense_rank": (
                        previous.dense_rank
                        if previous.dense_rank is not None
                        else hit.dense_rank
                    ),
                    "keyword_rank": (
                        previous.keyword_rank
                        if previous.keyword_rank is not None
                        else hit.keyword_rank
                    ),
                }
            )

    ordered = sorted(fused.items(), key=lambda item: item[1], reverse=True)
    return [
        representative[key].model_copy(update={"score": score, "score_kind": "rrf"})
        for key, score in ordered
    ]


__all__ = ["reciprocal_rank_fusion"]
