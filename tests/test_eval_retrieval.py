from __future__ import annotations

import asyncio

import pytest
from reactifact.eval import (
    EvalFailure,
    RetrievalCase,
    evaluate_retrieval,
    hit_rate_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_retrieval_metrics_deduplicate_and_cut_at_k() -> None:
    ranked = ["a", "b", "a", "c"]
    relevant = {"b", "c"}

    assert recall_at_k(ranked, relevant, k=3) == 1.0
    assert precision_at_k(ranked, relevant, k=3) == pytest.approx(2 / 3)
    assert hit_rate_at_k(ranked, relevant, k=1) == 0.0
    assert reciprocal_rank(ranked, relevant) == 0.5
    assert recall_at_k(ranked, set(), k=3) == 0.0


def test_ndcg_supports_graded_relevance() -> None:
    gains = {"a": 3.0, "b": 1.0, "c": 2.0}

    assert ndcg_at_k(["a", "c", "b"], gains, k=3) == pytest.approx(1.0)
    assert ndcg_at_k(["b", "a"], gains, k=2) == pytest.approx(0.6788, abs=1e-4)
    assert ndcg_at_k([], gains, k=2) == 0.0


def test_evaluate_retrieval_reports_and_skips() -> None:
    cases = [
        RetrievalCase(query="refund", relevant=["a"], name="easy"),
        RetrievalCase(query="install", relevant={"b": 2.0}),
        RetrievalCase(query="none", relevant=[]),
    ]
    results = {
        "refund": ["a", "b"],
        "install": ["x", "b"],
        "none": ["a"],
    }

    async def retrieve(query: str, limit: int) -> list[str]:
        return results[query][:limit]

    report = asyncio.run(evaluate_retrieval(cases, retrieve, k=2, key=str))

    assert report.retrieved["easy"] == ["a", "b"]
    assert report.results[2].skipped
    aggregate = report.aggregate()
    assert aggregate["recall"] == pytest.approx(1.0)
    assert aggregate["precision"] == pytest.approx(0.5)
    assert aggregate["mrr"] == pytest.approx(0.75)
    assert aggregate["ndcg"] == pytest.approx((1.0 + 0.6309) / 2, abs=1e-4)
    assert report.to_dict()["k"] == 2

    report.assert_passed({"recall": 1.0, "mrr": 0.7})
    with pytest.raises(EvalFailure):
        report.assert_passed({"recall": 1.1})


def test_evaluate_retrieval_accepts_a_sync_retriever() -> None:
    case = RetrievalCase(query="q", relevant=["a"])
    report = asyncio.run(
        evaluate_retrieval([case], lambda query, limit: ["b", "a"], k=2, key=str)
    )
    assert report.aggregate()["mrr"] == 0.5
