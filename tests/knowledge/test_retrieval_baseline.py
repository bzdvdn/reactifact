"""Offline retrieval baseline for the knowledge example corpus.

Uses `HashEmbedder`, so it needs no API key and no network; it is a regression
gate for ranking changes (RRF, filters, chunking), not an embedding benchmark.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from reactifact.eval import RetrievalCase
from reactifact.knowledge import (
    FilesystemConnector,
    HashEmbedder,
    KnowledgePipeline,
    MemoryIndex,
    evaluate_pipeline,
)

DOCS = Path(__file__).resolve().parents[2] / "examples" / "knowledge" / "docs"

CASES = [
    RetrievalCase("install the platform", ["guide/install.md"]),
    RetrievalCase("authentication token SSO", ["guide/auth.md"]),
    RetrievalCase("REST API pagination", ["guide/api.md"]),
    RetrievalCase("monitoring alerts retention", ["guide/monitoring.md"]),
    RetrievalCase("invoice payment autopay", ["pricing/billing.md"]),
    RetrievalCase("plans GPU inference pricing", ["pricing/tiers.md"]),
    RetrievalCase("what happens when payment fails", ["pricing/billing.md"]),
    RetrievalCase("connect SAML provider", ["guide/auth.md"]),
    RetrievalCase("CPU cores and RAM requirements", ["guide/install.md"]),
    RetrievalCase("response error 429", ["guide/api.md"]),
    RetrievalCase("extend data retention period", ["guide/monitoring.md"]),
    RetrievalCase("Starter Professional Enterprise plans", ["pricing/tiers.md"]),
]


def _pipeline() -> KnowledgePipeline:
    return KnowledgePipeline(
        FilesystemConnector(DOCS, source_id="docs"),
        embedder=HashEmbedder(dimensions=256),
        index=MemoryIndex(),
    )


def test_dense_retrieval_baseline() -> None:
    pipeline = _pipeline()
    asyncio.run(pipeline.run())

    report = asyncio.run(evaluate_pipeline(pipeline, CASES, k=5))
    aggregate = report.aggregate()

    assert aggregate["recall"] == 1.0
    assert aggregate["mrr"] >= 0.85
    assert aggregate["ndcg"] >= 0.9


def test_hybrid_retrieval_baseline() -> None:
    pipeline = _pipeline()
    asyncio.run(pipeline.run())

    report = asyncio.run(evaluate_pipeline(pipeline, CASES, k=5, hybrid=True))
    aggregate = report.aggregate()

    assert aggregate["recall"] == 1.0
    assert aggregate["mrr"] == 1.0
    assert aggregate["ndcg"] == 1.0
