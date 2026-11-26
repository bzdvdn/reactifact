"""Offline retrieval evaluation for the knowledge demo.

Uses `HashEmbedder`, so it needs no API key or network. It is a small example of
the `RetrievalCase` → `evaluate_pipeline` loop and a regression gate for changes
to ranking, chunking or MMR options.

    python -m examples.knowledge.eval
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from reactifact.eval import RetrievalCase, RetrievalReport
from reactifact.knowledge import (
    FilesystemConnector,
    HashEmbedder,
    KnowledgePipeline,
    MemoryIndex,
    evaluate_pipeline,
)

DOCS = Path(__file__).resolve().parent / "docs"

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


def build_pipeline() -> KnowledgePipeline:
    """A deterministic, offline pipeline over the demo corpus."""
    return KnowledgePipeline(
        FilesystemConnector(DOCS, source_id="docs"),
        embedder=HashEmbedder(dimensions=256),
        index=MemoryIndex(),
    )


async def evaluate(
    pipeline: KnowledgePipeline,
    *,
    hybrid: bool = False,
) -> RetrievalReport:
    """Score one retrieval mode against the demo cases."""
    return await evaluate_pipeline(
        pipeline,
        CASES,
        k=5,
        hybrid=hybrid,
    )


async def main() -> None:
    pipeline = build_pipeline()
    try:
        await pipeline.run()
        for hybrid in (False, True):
            report = await evaluate(pipeline, hybrid=hybrid)
            mode = "hybrid" if hybrid else "dense"
            print(f"{mode}: {report.aggregate()}")
            report.assert_passed({"recall": 0.9})
    finally:
        await pipeline.aclose()


if __name__ == "__main__":
    asyncio.run(main())
