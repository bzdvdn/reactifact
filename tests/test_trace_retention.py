"""Trace retention: max_runs, TTL, protected failures and tagged runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from reactifact.tracing import RetentionPolicy, RunTrace, TraceStore


def _trace(run_id: str, *, days_ago: float = 0, outcome: str = "completed"):
    return RunTrace(
        id=run_id,
        outcome=outcome,
        started_at=datetime.now(UTC) - timedelta(days=days_ago),
        duration_ms=1.0,
    )


def _store(tmp_path, policy: RetentionPolicy) -> TraceStore:
    return TraceStore(str(tmp_path / "t.db"), retention=policy)


def _export(store: TraceStore, *traces: RunTrace) -> None:
    for trace in traces:
        asyncio.run(store.export(trace))


def _ids(store: TraceStore) -> set[str]:
    return {item["id"] for item in asyncio.run(store.query(limit=100))["items"]}


def test_max_runs_keeps_the_newest(tmp_path):
    store = _store(tmp_path, RetentionPolicy(max_runs=None))
    _export(store, _trace("old", days_ago=2), _trace("new", days_ago=0))
    assert asyncio.run(store.prune(keep=1)) == 1
    assert _ids(store) == {"new"}
    store.close()


def test_ttl_removes_old_runs(tmp_path):
    store = _store(tmp_path, RetentionPolicy(max_runs=None))
    _export(store, _trace("old", days_ago=30), _trace("new", days_ago=1))
    store.retention = RetentionPolicy(max_runs=None, max_age_days=7)
    assert asyncio.run(store.prune()) == 1
    assert _ids(store) == {"new"}
    store.close()


def test_failed_runs_are_protected(tmp_path):
    store = _store(
        tmp_path, RetentionPolicy(max_runs=None, max_age_days=1, keep_failed=True)
    )
    _export(store, _trace("bad", days_ago=30, outcome="failed"))
    assert asyncio.run(store.prune()) == 0
    assert _ids(store) == {"bad"}
    store.close()


def test_tagged_runs_are_protected(tmp_path):
    store = _store(tmp_path, RetentionPolicy(max_runs=None))
    _export(store, _trace("incident", days_ago=30), _trace("normal", days_ago=30))
    asyncio.run(store.tag_runs(["incident"], "incident"))
    store.retention = RetentionPolicy(
        max_runs=None, max_age_days=1, keep_tags=frozenset({"incident"})
    )
    assert asyncio.run(store.prune()) == 1
    assert _ids(store) == {"incident"}
    store.close()


def test_dry_run_reports_without_deleting(tmp_path):
    store = _store(tmp_path, RetentionPolicy(max_runs=None))
    _export(store, _trace("a"), _trace("b"))
    assert asyncio.run(store.prune(keep=1, dry_run=True)) == 1
    assert _ids(store) == {"a", "b"}
    store.close()


def test_cli_prune_dry_run_and_real(tmp_path, capsys):
    from reactifact.cli import main

    path = str(tmp_path / "cli.db")
    store = TraceStore(path, retention=RetentionPolicy(max_runs=None))
    _export(store, _trace("old", days_ago=2), _trace("new"))
    store.close()

    assert main(["traces", "prune", path, "--keep", "1", "--dry-run"]) == 0
    assert "would delete 1" in capsys.readouterr().out
    store = TraceStore(path, retention=RetentionPolicy(max_runs=None))
    assert _ids(store) == {"old", "new"}
    store.close()

    assert main(["traces", "prune", path, "--keep", "1"]) == 0
    store = TraceStore(path, retention=RetentionPolicy(max_runs=None))
    assert _ids(store) == {"new"}
    store.close()
