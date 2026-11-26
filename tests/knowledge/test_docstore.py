from pathlib import Path

import pytest
from reactifact.knowledge import (
    DocEntry,
    DocStore,
    JsonDocStore,
    RunRecord,
    SqliteDocStore,
)


def _make_store(tmp_path: Path, kind: str) -> DocStore:
    if kind == "json":
        return JsonDocStore(tmp_path / "state.json")
    return SqliteDocStore(tmp_path / "state.sqlite3")


@pytest.mark.parametrize("kind", ["json", "sqlite"])
def test_docstore_roundtrip(tmp_path: Path, kind: str) -> None:
    store = _make_store(tmp_path, kind)
    assert store.entry("docs", "sig", "a.md") is None

    deleted = store.commit(
        "docs",
        "sig",
        {
            "a.md": DocEntry(content="h1", listing="l1"),
            "b.md": DocEntry(content="h2"),
        },
    )
    assert deleted == 0
    store.save()

    entry = store.entry("docs", "sig", "a.md")
    assert entry == DocEntry(content="h1", listing="l1")
    # A different pipeline signature must not reuse stale fingerprints.
    assert store.entry("docs", "other", "a.md") is None

    deleted = store.commit("docs", "sig", {"a.md": DocEntry(content="h3")})
    assert deleted == 1
    assert store.entry("docs", "sig", "a.md") == DocEntry(content="h3")


@pytest.mark.parametrize("kind", ["json", "sqlite"])
def test_docstore_run_ledger_and_snapshot(tmp_path: Path, kind: str) -> None:
    store = _make_store(tmp_path, kind)
    store.commit("docs", "sig", {"a.md": DocEntry(content="h1")})
    store.record_run(
        "docs",
        RunRecord(at="2026-01-01T00:00:00+00:00", ok=True, documents=1),
    )
    store.record_run(
        "docs",
        RunRecord(
            at="2026-01-02T00:00:00+00:00",
            ok=False,
            error="source is down",
        ),
    )

    runs = store.runs("docs")
    assert [run.ok for run in runs] == [False, True]
    assert store.last_run("docs") is not None
    assert store.last_run("docs").error == "source is down"

    snapshot = store.snapshot()
    assert [item["source_id"] for item in snapshot] == ["docs"]
    assert snapshot[0]["last_run"]["error"] == "source is down"
    assert len(snapshot[0]["runs"]) == 2


def test_sqlite_docstore_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    store = SqliteDocStore(path)
    store.commit("docs", "sig", {"a.md": DocEntry(content="h1")})
    store.record_run("docs", RunRecord(at="2026-01-01T00:00:00+00:00", ok=True))

    reopened = SqliteDocStore(path)
    assert reopened.entry("docs", "sig", "a.md") == DocEntry(content="h1")
    assert reopened.last_run("docs") is not None
    assert reopened.last_run("docs").ok is True
    assert reopened.sources() == [
        (
            "docs",
            {
                "signature": "sig",
                "documents": {"a.md": {"content": "h1", "listing": None}},
            },
        )
    ]


@pytest.mark.parametrize("kind", ["json", "sqlite"])
def test_docstore_keeps_recent_runs_only(tmp_path: Path, kind: str) -> None:
    store = _make_store(tmp_path, kind)
    for index in range(12):
        store.record_run("docs", RunRecord(at=str(index), ok=True))

    runs = store.runs("docs")
    assert len(runs) == 10
    assert runs[0].at == "11"
    assert runs[-1].at == "2"
