"""Retrieval evaluation adapters for `reactifact.knowledge`.

The metrics themselves live in `reactifact.eval.retrieval` and stay
knowledge-agnostic; these helpers only map `SearchHit`s to identifiers and
run a `KnowledgePipeline`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..eval.dataset import Dataset
from ..eval.retrieval import (
    RetrievalCase,
    RetrievalReport,
    evaluate_retrieval,
)
from .models import SearchHit
from .pipeline import KnowledgePipeline


def document_key(hit: SearchHit) -> str:
    """Relevance unit: the document, not the chunk."""
    for name in ("document_id", "documentId"):
        value = hit.metadata.get(name)
        if value:
            return str(value)
    return hit.source_uri


def chunk_key(hit: SearchHit) -> str:
    """Relevance unit: the stable chunk id."""
    return hit.chunk_id


def cases_from_dataset(
    dataset: Dataset,
    *,
    query_key: str = "query",
    relevant_key: str = "relevant",
) -> list[RetrievalCase]:
    """Build retrieval cases from dataset examples.

    Each example supplies `inputs[query_key]` and
    `reference_outputs[relevant_key]`; examples without either are skipped.
    """
    cases: list[RetrievalCase] = []
    for example in dataset:
        query = example.inputs.get(query_key)
        relevant = (example.reference_outputs or {}).get(relevant_key)
        if query is None or relevant is None:
            continue
        cases.append(
            RetrievalCase(
                query=str(query),
                relevant=relevant,
                name=example.id,
            )
        )
    return cases


async def evaluate_pipeline(
    pipeline: KnowledgePipeline,
    cases: Sequence[RetrievalCase],
    *,
    k: int = 5,
    key: Callable[[SearchHit], str] = document_key,
    **query_kwargs: Any,
) -> RetrievalReport:
    """Evaluate `pipeline.query` against retrieval cases.

    `query_kwargs` are forwarded to `query` (`hybrid=True`,
    `max_per_document=...`, `rerank=...`, `filters=...`, ...), so dense and
    hybrid configurations can be compared with the same cases.
    """

    async def retrieve(query: str, limit: int) -> list[SearchHit]:
        return await pipeline.query(query, top_k=limit, **query_kwargs)

    return await evaluate_retrieval(cases, retrieve, k=k, key=key)


__all__ = [
    "cases_from_dataset",
    "chunk_key",
    "document_key",
    "evaluate_pipeline",
]
