from __future__ import annotations

import asyncio
from typing import Any

import pytest
from reactifact.knowledge import ChromaIndex, Chunk


class FakeCollection:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for pid, embedding, document, metadata in zip(
            ids, embeddings, documents, metadatas, strict=True
        ):
            self.points[pid] = {
                "embedding": embedding,
                "document": document,
                "metadata": metadata,
            }

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        query = query_embeddings[0]
        items = list(self.points.items())
        if where:
            items = [
                (pid, point)
                for pid, point in items
                if all(
                    point["metadata"].get(key) == value for key, value in where.items()
                )
            ]
        scored = [
            (
                sum(a * b for a, b in zip(query, point["embedding"], strict=True)),
                pid,
                point,
            )
            for pid, point in items
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = scored[:n_results]
        return {
            "ids": [[pid for _, pid, _ in selected]],
            "documents": [[point["document"] for _, _, point in selected]],
            "metadatas": [[point["metadata"] for _, _, point in selected]],
            "distances": [[1.0 - score for score, _, _ in selected]],
        }

    def get(
        self,
        *,
        where: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        items = list(self.points.items())
        if where:
            items = [
                (pid, point)
                for pid, point in items
                if all(
                    point["metadata"].get(key) == value for key, value in where.items()
                )
            ]
        return {
            "ids": [pid for pid, _ in items],
            "metadatas": [point["metadata"] for _, point in items],
        }

    def delete(self, *, ids: list[str]) -> None:
        for pid in ids:
            self.points.pop(pid, None)

    def count(self) -> int:
        return len(self.points)


class FakeClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def get_or_create_collection(
        self, *, name: str, metadata: dict[str, Any]
    ) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


def test_chroma_index_roundtrip_and_metadata() -> None:
    index = ChromaIndex(collection="kb", client=FakeClient())
    asyncio.run(
        index.upsert(
            Chunk(
                text="refund policy",
                source_uri="a.md",
                metadata={"documentId": "a", "kind": "runbook", "n": 3},
            ),
            [1.0, 0.0],
            source_id="docs",
            artifact_hash="h1",
        )
    )
    asyncio.run(
        index.upsert(
            Chunk(
                text="install guide",
                source_uri="b.md",
                metadata={"documentId": "b", "kind": "guide"},
            ),
            [0.0, 1.0],
            source_id="docs",
            artifact_hash="h2",
        )
    )
    assert asyncio.run(index.count()) == 2

    hits = asyncio.run(index.search([1.0, 0.0], top_k=1))
    assert hits[0].text == "refund policy"
    assert hits[0].source_uri == "a.md"
    assert hits[0].metadata == {
        "documentId": "a",
        "kind": "runbook",
        "n": 3,
    }
    assert hits[0].score == pytest.approx(1.0)

    filtered = asyncio.run(index.search([1.0, 0.0], top_k=5, filters={"kind": "guide"}))
    assert [hit.source_uri for hit in filtered] == ["b.md"]


def test_chroma_index_acl_and_reconcile() -> None:
    index = ChromaIndex(collection="kb", client=FakeClient())
    asyncio.run(
        index.upsert(
            Chunk(
                text="internal",
                source_uri="a.md",
                metadata={"acl": ["eng"]},
            ),
            [1.0, 0.0],
            source_id="docs",
            artifact_hash="h1",
        )
    )
    asyncio.run(
        index.upsert(
            Chunk(text="public", source_uri="b.md", metadata={}),
            [0.0, 1.0],
            source_id="docs",
            artifact_hash="h2",
        )
    )

    visible = asyncio.run(index.search([1.0, 0.0], top_k=5, principals=["ops"]))
    assert [hit.source_uri for hit in visible] == ["b.md"]

    allowed = asyncio.run(index.search([1.0, 0.0], top_k=5, principals=["eng"]))
    assert {hit.source_uri for hit in allowed} == {"a.md", "b.md"}

    asyncio.run(index.reconcile("docs", {"a.md": "h1"}))
    assert asyncio.run(index.count()) == 1


def test_chroma_index_rejects_reserved_metadata() -> None:
    index = ChromaIndex(collection="kb", client=FakeClient())
    with pytest.raises(ValueError, match="reserved"):
        asyncio.run(
            index.upsert(
                Chunk(
                    text="x",
                    source_uri="a.md",
                    metadata={"__bad": "value"},
                ),
                [1.0, 0.0],
                source_id="docs",
                artifact_hash="h1",
            )
        )
