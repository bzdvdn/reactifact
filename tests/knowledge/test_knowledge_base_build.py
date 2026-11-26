from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pytest
from examples.knowledge_base.build import _run


def _args(tmp_path: Path, store: str) -> argparse.Namespace:
    docs = tmp_path / f"docs-{store}"
    docs.mkdir()
    (docs / "a.md").write_text("# Refunds\n\nrefund policy", encoding="utf-8")
    return argparse.Namespace(
        docs=str(docs),
        db=str(tmp_path / f"index-{store}.db"),
        state=str(
            tmp_path / f"state-{store}.sqlite3"
            if store == "sqlite"
            else tmp_path / f"state-{store}.json"
        ),
        store=store,
        cache=str(tmp_path / f"cache-{store}"),
        query="refund",
        top_k=2,
        max_tokens=100,
        qdrant_url="",
        collection="docs",
    )


@pytest.mark.parametrize("store", ["json", "sqlite"])
def test_knowledge_base_build_supports_both_stores(
    tmp_path: Path,
    store: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The demo may run with a real provider in a developer's environment;
    # this test must stay offline and deterministic.
    monkeypatch.setattr("examples.knowledge_base.build.embedder_from_env", lambda: None)
    args = _args(tmp_path, store)

    asyncio.run(_run(args))

    assert Path(args.state).is_file()
