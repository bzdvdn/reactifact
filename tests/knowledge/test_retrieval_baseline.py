"""Offline retrieval baseline for the knowledge example corpus.

Uses `HashEmbedder`, so it needs no API key and no network; it is a regression
gate for ranking changes (RRF, filters, chunking), not an embedding benchmark.
"""

from __future__ import annotations

import asyncio

from examples.knowledge.eval import CASES, build_pipeline
from reactifact.knowledge import evaluate_pipeline


def test_dense_retrieval_baseline() -> None:
    pipeline = build_pipeline()
    asyncio.run(pipeline.run())

    report = asyncio.run(evaluate_pipeline(pipeline, CASES, k=5))
    aggregate = report.aggregate()

    assert aggregate["recall"] == 1.0
    assert aggregate["mrr"] >= 0.85
    assert aggregate["ndcg"] >= 0.9


def test_hybrid_retrieval_baseline() -> None:
    pipeline = build_pipeline()
    asyncio.run(pipeline.run())

    report = asyncio.run(evaluate_pipeline(pipeline, CASES, k=5, hybrid=True))
    aggregate = report.aggregate()

    assert aggregate["recall"] == 1.0
    assert aggregate["mrr"] == 1.0
    assert aggregate["ndcg"] == 1.0
