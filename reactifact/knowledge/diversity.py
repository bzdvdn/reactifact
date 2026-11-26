"""Maximal Marginal Relevance selection for diverse retrieval.

`max_per_document` is a hard duplicate guard; MMR is a soft diversity
objective: each next hit should be relevant to the query *and* dissimilar to
the hits already selected. It needs candidate vectors — the pipeline computes
them with its embedder, so the index contract stays unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .errors import ConfigError
from .indexes.math import cosine_similarity
from .models import SearchHit


@dataclass(frozen=True)
class MMR:
    """Maximal Marginal Relevance settings.

    `lambda_=1` keeps pure relevance; `lambda_=0` maximizes novelty only.
    Values in between balance the two, with `0.5` the common starting point.
    """

    lambda_: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= self.lambda_ <= 1.0:
            raise ConfigError("MMR.lambda_ must be between 0 and 1")


def mmr_select(
    query_vector: Sequence[float],
    hits: Sequence[SearchHit],
    vectors: Sequence[Sequence[float]],
    *,
    limit: int,
    lambda_: float,
) -> list[SearchHit]:
    """Greedily pick `limit` hits balancing relevance and novelty.

    Ties keep the original retrieval order, so the result is deterministic.
    """
    if limit < 1:
        raise ConfigError("MMR limit must be >= 1")
    if len(hits) != len(vectors):
        raise ConfigError("MMR needs one vector per candidate hit")
    if not hits:
        return []

    query = list(query_vector)
    candidate_vectors = [list(vector) for vector in vectors]
    relevance = [cosine_similarity(query, vector) for vector in candidate_vectors]
    selected: list[int] = []
    remaining = list(range(len(hits)))

    while remaining and len(selected) < limit:
        if not selected:
            best = max(remaining, key=lambda index: relevance[index])
        else:
            best = max(
                remaining,
                key=lambda index: (
                    lambda_ * relevance[index]
                    - (1.0 - lambda_)
                    * max(
                        cosine_similarity(
                            candidate_vectors[index],
                            candidate_vectors[chosen],
                        )
                        for chosen in selected
                    )
                ),
            )
        selected.append(best)
        remaining.remove(best)

    return [hits[index] for index in selected]


__all__ = ["MMR", "mmr_select"]
