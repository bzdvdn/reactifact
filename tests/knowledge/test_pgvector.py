from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid

import pytest
from reactifact.knowledge import Chunk, PgVectorIndex

PG_DSN = os.environ.get("TEST_PG_DSN")


def test_pgvector_requires_dsn() -> None:
    with pytest.raises(ValueError, match="requires a 'dsn'"):
        PgVectorIndex()


def test_pgvector_rejects_dsn_and_env_together() -> None:
    with pytest.raises(ValueError, match="either dsn or dsn_env"):
        PgVectorIndex(dsn="postgresql://x", dsn_env="TEST_PG_DSN")


def test_pgvector_requires_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REACTIFACT_TEST_MISSING_DSN", raising=False)
    with pytest.raises(ValueError, match="is not set"):
        PgVectorIndex(dsn_env="REACTIFACT_TEST_MISSING_DSN")


@pytest.mark.skipif(
    importlib.util.find_spec("psycopg") is not None,
    reason="psycopg is installed; the graceful-missing-extra path is not testable",
)
def test_pgvector_reports_missing_pg_extra() -> None:
    with pytest.raises(ImportError, match="pg"):
        PgVectorIndex(dsn="postgresql://localhost/test")


@pytest.mark.skipif(PG_DSN is None, reason="TEST_PG_DSN not set")
def test_pgvector_roundtrip() -> None:
    pytest.importorskip("psycopg")
    table = f"reactifact_test_{uuid.uuid4().hex[:10]}"
    index = PgVectorIndex(
        PG_DSN,
        table=table,
        text_search_config="simple",
    )

    async def run() -> None:
        try:
            await index.upsert(
                Chunk(
                    text="refund policy",
                    source_uri="a.md",
                    metadata={"documentId": "a"},
                ),
                [1.0, 0.0],
                source_id="docs",
                artifact_hash="h1",
            )
            await index.upsert(
                Chunk(
                    text="install guide",
                    source_uri="b.md",
                    metadata={"documentId": "b"},
                ),
                [0.0, 1.0],
                source_id="docs",
                artifact_hash="h2",
            )
            hits = await index.search([1.0, 0.0], top_k=1)
            assert hits[0].source_uri == "a.md"
            assert hits[0].metadata == {"documentId": "a"}
            keyword = await index.keyword_search("refund", top_k=1)
            assert keyword[0].source_uri == "a.md"
            assert await index.count() == 2
            await index.reconcile("docs", {"a.md": "h1"})
            assert await index.count() == 1
        finally:
            await index.aclose()

    asyncio.run(run())
