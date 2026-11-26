from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from reactifact.knowledge import (
    Chunk,
    DocStore,
    JsonDocStore,
    MemoryIndex,
    RunRecord,
)
from reactifact.knowledge.web import create_knowledge_router


def _store(tmp_path: Path) -> DocStore:
    store = JsonDocStore(tmp_path / "state.json")
    store.record_run(
        "docs",
        RunRecord(
            at="2026-01-01T00:00:00+00:00",
            ok=True,
            duration_seconds=1.25,
            documents=3,
            chunks=9,
            changed=1,
            skipped=2,
            deleted=0,
            embed_cache_hits=7,
            embed_cache_misses=2,
        ),
    )
    store.record_run(
        "docs",
        RunRecord(
            at="2026-01-02T00:00:00+00:00",
            ok=False,
            error="source is down",
            duration_seconds=0.5,
        ),
    )
    return store


def _client(store: DocStore, index: MemoryIndex | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_knowledge_router(store, index=index),
        prefix="/knowledge",
    )
    return TestClient(app)


def test_knowledge_status_endpoint(tmp_path: Path) -> None:
    index = MemoryIndex()
    asyncio.run(
        index.upsert(
            Chunk(text="hello", source_uri="a"),
            [1.0, 0.0],
            source_id="docs",
            artifact_hash="h1",
        )
    )
    client = _client(_store(tmp_path), index)
    response = client.get("/knowledge/api/knowledge/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["index"] == {"type": "MemoryIndex", "count": 1}
    assert payload["sources"][0]["source_id"] == "docs"
    assert payload["sources"][0]["last_run"]["error"] == "source is down"
    assert len(payload["sources"][0]["runs"]) == 2


def test_knowledge_page_renders(tmp_path: Path) -> None:
    client = _client(_store(tmp_path))
    response = client.get("/knowledge/")
    assert response.status_code == 200
    assert "reactifact knowledge" in response.text
    assert "/api/knowledge/status" in response.text
    assert client.get("/knowledge").status_code == 200


def test_knowledge_page_without_runs(tmp_path: Path) -> None:
    client = _client(JsonDocStore(tmp_path / "empty.json"))
    response = client.get("/knowledge/")
    assert response.status_code == 200
    assert "reactifact knowledge" in response.text
