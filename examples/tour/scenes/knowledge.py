"""knowledge — offline ingestion, hybrid retrieval and retrieval metrics."""

from __future__ import annotations

from typing import Any

from reactifact.eval import RetrievalCase
from reactifact.knowledge import (
    HashEmbedder,
    KnowledgePipeline,
    MemoryIndex,
    RawDocument,
    evaluate_pipeline,
)


class DictConnector:
    """A tiny in-memory connector so the scene is fully self-contained."""

    source_id = "tour"

    def __init__(self, docs: dict[str, str]) -> None:
        self.docs = docs

    async def fetch(self) -> list[RawDocument]:
        return [
            RawDocument.from_text(step_id=uri, uri=uri, text=text, title=uri)
            for uri, text in self.docs.items()
        ]


async def run_knowledge_scene() -> dict[str, Any]:
    pipeline = KnowledgePipeline(
        DictConnector(
            {
                "refund.md": "# Refunds\n\nRefunds within 30 days.\n\n"
                "# Billing\n\nInvoices are monthly.",
                "install.md": "# Install\n\nRun pip install reactifact.",
                "monitoring.md": "# Monitoring\n\nAlerts and metrics retention.",
            }
        ),
        embedder=HashEmbedder(dimensions=64),
        index=MemoryIndex(),
    )
    try:
        report = await pipeline.run()
        hits = await pipeline.query(
            "refund policy",
            top_k=3,
            hybrid=True,
            max_per_document=1,
        )
        metrics = await evaluate_pipeline(
            pipeline,
            [RetrievalCase("refund policy", ["refund.md"])],
            k=3,
            hybrid=True,
        )
        aggregate = metrics.aggregate()
        return {
            "documents": report.documents_ingested,
            "chunks": report.chunks_indexed,
            "hits": [hit.source_uri for hit in hits],
            "score_kind": hits[0].score_kind if hits else "",
            "recall": aggregate.get("recall", 0.0),
            "mrr": aggregate.get("mrr", 0.0),
        }
    finally:
        await pipeline.aclose()
