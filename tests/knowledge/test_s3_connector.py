from __future__ import annotations

import asyncio
import io

import pytest
from reactifact.knowledge import (
    HashEmbedder,
    KnowledgePipeline,
    MemoryIndex,
    S3Connector,
    SourceError,
)


class _Paginator:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages

    def paginate(self, **kwargs) -> object:
        return iter(self.pages)


class FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.get_calls: list[str] = []

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "list_objects_v2"
        page = {
            "Contents": [{"Key": key, "ETag": f'"etag-{key}"'} for key in self.objects]
        }
        return _Paginator([page])

    def get_object(self, Bucket: str, Key: str) -> dict:
        self.get_calls.append(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


def test_s3_connector_lists_and_fetches_objects() -> None:
    client = FakeS3(
        {
            "docs/guide.md": b"# Guide\n\nrefund policy",
            "docs/manual.pdf": b"%PDF",
            "docs/skip.exe": b"binary",
        }
    )
    connector = S3Connector(
        "bucket",
        prefix="docs/",
        source_id="s3",
        extensions=(".md", ".pdf"),
        client=client,
    )

    metas = asyncio.run(connector.list_documents())
    assert [meta.uri for meta in metas] == ["docs/guide.md", "docs/manual.pdf"]
    assert metas[0].fingerprint == "etag-docs/guide.md"
    assert metas[0].content_type == "text/markdown"
    assert metas[1].content_type == "application/pdf"

    documents = asyncio.run(connector.fetch(uris={"docs/guide.md"}))
    assert [document.uri for document in documents] == ["docs/guide.md"]
    assert documents[0].metadata["sourceUrl"] == "s3://bucket/docs/guide.md"
    assert documents[0].metadata["title"] == "guide"
    assert client.get_calls == ["docs/guide.md"]


def test_s3_connector_incremental_run_skips_unchanged_objects(tmp_path) -> None:
    client = FakeS3({"guide.md": b"# Guide\n\nrefund policy"})
    connector = S3Connector("bucket", client=client)
    pipeline = KnowledgePipeline(
        connector,
        embedder=HashEmbedder(dimensions=16),
        index=MemoryIndex(),
    )
    state = f"{tmp_path}/state.json"

    first = asyncio.run(pipeline.run(incremental=True, state_path=state))
    second = asyncio.run(pipeline.run(incremental=True, state_path=state))

    assert first.documents_changed == 1
    assert second.documents_skipped == 1
    assert client.get_calls == ["guide.md"]


def test_s3_connector_wraps_provider_errors() -> None:
    class Broken(FakeS3):
        def get_object(self, Bucket: str, Key: str) -> dict:
            raise RuntimeError("access denied")

    connector = S3Connector("bucket", client=Broken({"a.md": b"x"}))
    with pytest.raises(SourceError, match="access denied"):
        asyncio.run(connector.fetch(uris={"a.md"}))
