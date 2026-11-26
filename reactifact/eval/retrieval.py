"""Deterministic retrieval metrics (§56).

Retrieval is scored before an answer exists, so it cannot live behind
`Context`-based metrics. These functions take a ranked list of identifiers and
ground-truth relevant identifiers; `evaluate_retrieval` adds the runner and
report on top. The module is knowledge-agnostic: a `key` callable maps whatever
the retriever returns (a `SearchHit`, a `SourceRef`, a plain id) to the
identifier used by the ground truth.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import (
    Awaitable,
    Callable,
    Collection,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from typing import Any

from .scoring import EvalReport, EvalResult, Metric

_METRIC_NAMES = ("recall", "precision", "mrr", "ndcg", "hit_rate")


def _unique(ranked: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in ranked:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def recall_at_k(
    ranked: Sequence[str],
    relevant: Collection[str],
    *,
    k: int,
) -> float:
    """Share of relevant identifiers present in the first `k` results."""
    if not relevant:
        return 0.0
    top = _unique(ranked)[:k]
    return sum(1 for item in top if item in relevant) / len(relevant)


def precision_at_k(
    ranked: Sequence[str],
    relevant: Collection[str],
    *,
    k: int,
) -> float:
    """Share of the first `k` slots filled by relevant identifiers."""
    if k < 1:
        return 0.0
    top = _unique(ranked)[:k]
    return sum(1 for item in top if item in relevant) / k


def hit_rate_at_k(
    ranked: Sequence[str],
    relevant: Collection[str],
    *,
    k: int,
) -> float:
    """1.0 when at least one relevant identifier is in the first `k` results."""
    if not relevant:
        return 0.0
    top = _unique(ranked)[:k]
    return 1.0 if any(item in relevant for item in top) else 0.0


def reciprocal_rank(
    ranked: Sequence[str],
    relevant: Collection[str],
) -> float:
    """Reciprocal rank of the first relevant identifier (0.0 when absent)."""
    if not relevant:
        return 0.0
    for rank, item in enumerate(_unique(ranked), start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    ranked: Sequence[str],
    gains: Mapping[str, float],
    *,
    k: int,
) -> float:
    """Normalized discounted cumulative gain with graded relevance.

    `gains` maps an identifier to its gain; an identifier missing from `gains`
    is irrelevant. Identifiers are deduplicated (first occurrence wins), so a
    retriever cannot inflate DCG by repeating the same result.
    """
    if not gains or k < 1:
        return 0.0
    top = _unique(ranked)[:k]
    dcg = sum(
        float(gains[item]) / math.log2(rank + 1)
        for rank, item in enumerate(top, start=1)
        if item in gains
    )
    ideal = sorted((float(value) for value in gains.values()), reverse=True)[:k]
    idcg = sum(value / math.log2(rank + 1) for rank, value in enumerate(ideal, start=1))
    return dcg / idcg if idcg > 0 else 0.0


@dataclass(frozen=True)
class RetrievalCase:
    """One query plus the identifiers that should be retrieved for it."""

    query: str
    relevant: Mapping[str, float] | Sequence[str]
    name: str = ""

    def gains(self) -> dict[str, float]:
        """Normalize binary or graded relevance into `{id: gain}`."""
        if isinstance(self.relevant, Mapping):
            return {str(key): float(value) for key, value in self.relevant.items()}
        return {str(item): 1.0 for item in self.relevant}


@dataclass
class RetrievalReport(EvalReport):
    """Per-case retrieval metrics, aggregate scores and the retrieved ids."""

    k: int = 5
    retrieved: dict[str, list[str]] = field(default_factory=dict)

    def render(self) -> str:
        """Human-readable report, including the `k` used."""
        return f"retrieval · k={self.k}\n\n" + super().render()

    def to_dict(self) -> dict[str, Any]:
        """Serialize metrics, `k` and the retrieved ids."""
        return {**super().to_dict(), "k": self.k, "retrieved": self.retrieved}


async def evaluate_retrieval(
    cases: Sequence[RetrievalCase],
    retrieve: Callable[
        [str, int],
        Sequence[Any] | Awaitable[Sequence[Any]],
    ],
    *,
    k: int = 5,
    key: Callable[[Any], str],
) -> RetrievalReport:
    """Run every case through `retrieve` and score recall/precision/MRR/nDCG.

    `retrieve(query, limit)` may be sync or async; `key` maps each returned
    item to its ground-truth identifier. A case without relevant ids is
    skipped rather than scored as zero.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    report = RetrievalReport(k=k)
    for case in cases:
        name = case.name or case.query
        gains = case.gains()
        if not gains:
            report.results.append(EvalResult(case=name, skipped=list(_METRIC_NAMES)))
            continue
        outcome = retrieve(case.query, k)
        items = await outcome if inspect.isawaitable(outcome) else outcome
        ranked = _unique([str(key(item)) for item in items])[:k]
        relevant = set(gains)
        report.retrieved[name] = ranked
        report.results.append(
            EvalResult(
                case=name,
                metrics=[
                    Metric("recall", recall_at_k(ranked, relevant, k=k)),
                    Metric("precision", precision_at_k(ranked, relevant, k=k)),
                    Metric("mrr", reciprocal_rank(ranked, relevant)),
                    Metric("ndcg", ndcg_at_k(ranked, gains, k=k)),
                    Metric("hit_rate", hit_rate_at_k(ranked, relevant, k=k)),
                ],
            )
        )
    return report


__all__ = [
    "RetrievalCase",
    "RetrievalReport",
    "evaluate_retrieval",
    "hit_rate_at_k",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
]
