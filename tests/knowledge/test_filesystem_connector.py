from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from reactifact.knowledge import FilesystemConnector, SourceError


def _docs(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    (root / "guide").mkdir(parents=True)
    (root / "guide" / "auth.md").write_text("# Auth\n\nUse an API key.")
    (root / "guide" / "install.txt").write_text("Install it.")
    (root / "guide" / "image.png").write_bytes(b"not text")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "secret.md").write_text("# Secret")
    return root


def test_filesystem_connector_lists_and_fetches(tmp_path: Path) -> None:
    connector = FilesystemConnector(_docs(tmp_path), source_id="docs")
    metas = asyncio.run(connector.list_documents())
    assert [meta.uri for meta in metas] == [
        "guide/auth.md",
        "guide/install.txt",
    ]
    assert all(len(meta.fingerprint) == 64 for meta in metas)

    documents = asyncio.run(connector.fetch())
    assert [document.uri for document in documents] == [
        "guide/auth.md",
        "guide/install.txt",
    ]
    auth = documents[0]
    assert auth.data == b"# Auth\n\nUse an API key."
    assert auth.metadata["title"] == "auth"
    assert auth.metadata["sourceUrl"].endswith("guide/auth.md")


def test_filesystem_connector_incremental_fetch(tmp_path: Path) -> None:
    connector = FilesystemConnector(_docs(tmp_path))
    documents = asyncio.run(connector.fetch(uris={"guide/install.txt"}))
    assert [document.uri for document in documents] == ["guide/install.txt"]


def test_filesystem_connector_include_hidden(tmp_path: Path) -> None:
    root = _docs(tmp_path)
    connector = FilesystemConnector(root, include_hidden=True)
    metas = asyncio.run(connector.list_documents())
    assert ".hidden/secret.md" in {meta.uri for meta in metas}


def test_filesystem_connector_extensions(tmp_path: Path) -> None:
    connector = FilesystemConnector(_docs(tmp_path), extensions=(".txt",))
    metas = asyncio.run(connector.list_documents())
    assert [meta.uri for meta in metas] == ["guide/install.txt"]


def test_filesystem_connector_missing_root_fails_fast(tmp_path: Path) -> None:
    connector = FilesystemConnector(tmp_path / "missing")
    with pytest.raises(SourceError, match="does not exist"):
        asyncio.run(connector.list_documents())
