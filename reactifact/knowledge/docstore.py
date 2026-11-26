"""Per-source document state for incremental ingestion.

`DocStore` is the contract; `JsonDocStore` is the zero-infrastructure default
and `SqliteDocStore` is the durable backend for larger corpora. Both expose
the same surface, so the pipeline and the status router are backend-agnostic.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

_VERSION = 2
_RUN_HISTORY = 10
_SQLITE_VERSION = 1

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    source_id TEXT NOT NULL,
    uri       TEXT NOT NULL,
    content   TEXT NOT NULL,
    listing   TEXT,
    PRIMARY KEY (source_id, uri)
);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);
CREATE TABLE IF NOT EXISTS runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id          TEXT NOT NULL,
    at                 TEXT NOT NULL,
    ok                 INTEGER NOT NULL,
    error              TEXT NOT NULL DEFAULT '',
    duration_seconds   REAL NOT NULL DEFAULT 0,
    documents          INTEGER NOT NULL DEFAULT 0,
    chunks             INTEGER NOT NULL DEFAULT 0,
    changed            INTEGER NOT NULL DEFAULT 0,
    skipped            INTEGER NOT NULL DEFAULT 0,
    deleted            INTEGER NOT NULL DEFAULT 0,
    embed_cache_hits   INTEGER NOT NULL DEFAULT 0,
    embed_cache_misses INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_source ON runs(source_id, id DESC);
"""

_RUN_COLUMNS = (
    "at, ok, error, duration_seconds, documents, chunks, changed, skipped, "
    "deleted, embed_cache_hits, embed_cache_misses"
)


@dataclass(frozen=True)
class DocEntry:
    """Stored fingerprints of one document from the last successful run."""

    content: str
    listing: str | None = None


@dataclass
class RunRecord:
    """Outcome of one pipeline run, kept per source for visibility."""

    at: str
    ok: bool
    error: str = ""
    duration_seconds: float = 0.0
    documents: int = 0
    chunks: int = 0
    changed: int = 0
    skipped: int = 0
    deleted: int = 0
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0

    @staticmethod
    def from_dict(value: dict[str, Any]) -> RunRecord:
        return RunRecord(
            at=str(value.get("at", "")),
            ok=bool(value.get("ok", False)),
            error=str(value.get("error", "")),
            duration_seconds=float(value.get("duration_seconds", 0.0)),
            documents=int(value.get("documents", 0)),
            chunks=int(value.get("chunks", 0)),
            changed=int(value.get("changed", 0)),
            skipped=int(value.get("skipped", 0)),
            deleted=int(value.get("deleted", 0)),
            embed_cache_hits=int(value.get("embed_cache_hits", 0)),
            embed_cache_misses=int(value.get("embed_cache_misses", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "ok": self.ok,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "documents": self.documents,
            "chunks": self.chunks,
            "changed": self.changed,
            "skipped": self.skipped,
            "deleted": self.deleted,
            "embed_cache_hits": self.embed_cache_hits,
            "embed_cache_misses": self.embed_cache_misses,
        }


class DocStore(Protocol):
    """Incremental-ingestion state: per-source fingerprints plus a run ledger."""

    path: Path

    def entry(self, source_id: str, signature: str, uri: str) -> DocEntry | None: ...

    def commit(
        self,
        source_id: str,
        signature: str,
        current: Mapping[str, DocEntry],
    ) -> int: ...

    def save(self) -> None: ...

    def record_run(self, source_id: str, record: RunRecord) -> None: ...

    def runs(self, source_id: str) -> list[RunRecord]: ...

    def last_run(self, source_id: str) -> RunRecord | None: ...

    def sources(self) -> list[tuple[str, dict[str, Any]]]: ...

    def snapshot(self, *, history: int = _RUN_HISTORY) -> list[dict[str, Any]]: ...


class JsonDocStore:
    """JSON-backed map of `source_id -> {uri -> DocEntry}`.

    State is keyed by source identity **and** a pipeline signature. Changing
    extraction/chunking/embedding settings invalidates stored fingerprints
    instead of silently serving stale chunks. State is committed only after a
    successful run. The whole file is rewritten on save; use
    `SqliteDocStore` for large corpora.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._sources: dict[str, dict[str, Any]] = self._load()
        self._runs: dict[str, list[RunRecord]] = self._load_runs()

    @staticmethod
    def default_path() -> Path:
        return Path(".reactifact") / "knowledge" / "state.json"

    def entry(self, source_id: str, signature: str, uri: str) -> DocEntry | None:
        entry = self._sources.get(source_id)
        if entry is None or entry.get("signature") != signature:
            return None
        documents = entry.get("documents")
        if not isinstance(documents, dict):
            return None
        value = documents.get(uri)
        if value is None:
            return None
        return _decode_entry(value)

    def commit(
        self,
        source_id: str,
        signature: str,
        current: Mapping[str, DocEntry],
    ) -> int:
        previous = self._sources.get(source_id, {}).get("documents", {})
        if not isinstance(previous, dict):
            previous = {}
        deleted = sum(1 for uri in previous if uri not in current)
        self._sources[source_id] = {
            "signature": signature,
            "documents": {
                uri: {
                    "content": entry.content,
                    "listing": entry.listing,
                }
                for uri, entry in current.items()
            },
        }
        return deleted

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                {
                    "version": _VERSION,
                    "sources": self._sources,
                    "runs": {
                        source_id: [run.to_dict() for run in history]
                        for source_id, history in self._runs.items()
                    },
                },
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.path)

    def record_run(self, source_id: str, record: RunRecord) -> None:
        history = self._runs.setdefault(source_id, [])
        history.insert(0, record)
        del history[_RUN_HISTORY:]
        self.save()

    def runs(self, source_id: str) -> list[RunRecord]:
        return list(self._runs.get(source_id, []))

    def last_run(self, source_id: str) -> RunRecord | None:
        history = self._runs.get(source_id)
        return history[0] if history else None

    def sources(self) -> list[tuple[str, dict[str, Any]]]:
        return sorted(self._sources.items())

    def snapshot(self, *, history: int = _RUN_HISTORY) -> list[dict[str, Any]]:
        """Per-source status plus recent runs, ready for a JSON endpoint/UI."""
        return _snapshot(
            set(self._sources) | set(self._runs),
            self.runs,
            history,
        )

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        sources = data.get("sources")
        if not isinstance(sources, dict):
            return {}
        loaded: dict[str, dict[str, Any]] = {}
        for source_id, entry in sources.items():
            if isinstance(entry, dict):
                loaded[str(source_id)] = entry
        return loaded

    def _load_runs(self) -> dict[str, list[RunRecord]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        runs = data.get("runs")
        if not isinstance(runs, dict):
            return {}
        loaded: dict[str, list[RunRecord]] = {}
        for source_id, history in runs.items():
            if not isinstance(history, list):
                continue
            records = [
                RunRecord.from_dict(record)
                for record in history
                if isinstance(record, dict)
            ]
            if records:
                loaded[str(source_id)] = records
        return loaded


class SqliteDocStore:
    """SQLite-backed incremental state for large corpora.

    Writes are transactional and per-row instead of rewriting a whole JSON
    file, so state stays cheap as the document count grows. The schema is
    created on demand; `save()` is a no-op because `commit`/`record_run`
    already commit their transaction.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    @staticmethod
    def default_path() -> Path:
        return Path(".reactifact") / "knowledge" / "state.sqlite3"

    def entry(self, source_id: str, signature: str, uri: str) -> DocEntry | None:
        connection = self._get_connection()
        with self._lock:
            row = connection.execute(
                "SELECT d.content, d.listing FROM documents AS d "
                "JOIN sources AS s ON s.source_id = d.source_id "
                "WHERE d.source_id = ? AND d.uri = ? AND s.signature = ?",
                (source_id, uri, signature),
            ).fetchone()
        if row is None:
            return None
        return DocEntry(
            content=str(row[0]),
            listing=str(row[1]) if row[1] is not None else None,
        )

    def commit(
        self,
        source_id: str,
        signature: str,
        current: Mapping[str, DocEntry],
    ) -> int:
        connection = self._get_connection()
        with self._lock, connection:
            previous = {
                row[0]
                for row in connection.execute(
                    "SELECT uri FROM documents WHERE source_id = ?",
                    (source_id,),
                )
            }
            deleted = sum(1 for uri in previous if uri not in current)
            connection.execute(
                "DELETE FROM documents WHERE source_id = ?",
                (source_id,),
            )
            connection.executemany(
                "INSERT INTO documents (source_id, uri, content, listing) "
                "VALUES (?, ?, ?, ?)",
                [
                    (source_id, uri, entry.content, entry.listing)
                    for uri, entry in current.items()
                ],
            )
            connection.execute(
                "INSERT INTO sources (source_id, signature) VALUES (?, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET signature = excluded.signature",
                (source_id, signature),
            )
        return deleted

    def save(self) -> None:
        """No-op: SQLite transactions are committed by `commit`/`record_run`."""

    def record_run(self, source_id: str, record: RunRecord) -> None:
        connection = self._get_connection()
        with self._lock, connection:
            connection.execute(
                f"INSERT INTO runs (source_id, {_RUN_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source_id, *_run_values(record)),
            )
            connection.execute(
                "DELETE FROM runs WHERE source_id = ? AND id NOT IN ("
                "SELECT id FROM runs WHERE source_id = ? "
                "ORDER BY id DESC LIMIT ?)",
                (source_id, source_id, _RUN_HISTORY),
            )

    def runs(self, source_id: str) -> list[RunRecord]:
        connection = self._get_connection()
        with self._lock:
            rows = connection.execute(
                f"SELECT {_RUN_COLUMNS} FROM runs WHERE source_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (source_id, _RUN_HISTORY),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def last_run(self, source_id: str) -> RunRecord | None:
        history = self.runs(source_id)
        return history[0] if history else None

    def sources(self) -> list[tuple[str, dict[str, Any]]]:
        connection = self._get_connection()
        with self._lock:
            rows = connection.execute(
                "SELECT s.source_id, s.signature, d.uri, d.content, d.listing "
                "FROM sources AS s "
                "LEFT JOIN documents AS d ON d.source_id = s.source_id "
                "ORDER BY s.source_id, d.uri"
            ).fetchall()
        grouped: dict[str, dict[str, Any]] = {}
        for source_id, signature, uri, content, listing in rows:
            block = grouped.setdefault(
                str(source_id),
                {"signature": signature, "documents": {}},
            )
            if uri is not None:
                block["documents"][uri] = {
                    "content": content,
                    "listing": listing,
                }
        return sorted(grouped.items())

    def snapshot(self, *, history: int = _RUN_HISTORY) -> list[dict[str, Any]]:
        """Per-source status plus recent runs, ready for a JSON endpoint/UI."""
        connection = self._get_connection()
        with self._lock:
            rows = connection.execute(
                "SELECT source_id FROM sources UNION SELECT source_id FROM runs"
            ).fetchall()
        return _snapshot({str(row[0]) for row in rows}, self.runs, history)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(_SQLITE_SCHEMA)
        connection.execute(f"PRAGMA user_version = {_SQLITE_VERSION}")
        connection.commit()
        return connection

    def _get_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            with self._lock:
                if self._connection is None:
                    self._connection = self._connect()
        return self._connection


def _snapshot(
    source_ids: Iterable[str],
    runs_of: Callable[[str], list[RunRecord]],
    history: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source_id in sorted(source_ids):
        runs = runs_of(source_id)[:history]
        out.append(
            {
                "source_id": source_id,
                "last_run": runs[0].to_dict() if runs else None,
                "runs": [run.to_dict() for run in runs],
            }
        )
    return out


def _run_values(record: RunRecord) -> tuple[object, ...]:
    return (
        record.at,
        int(record.ok),
        record.error,
        record.duration_seconds,
        record.documents,
        record.chunks,
        record.changed,
        record.skipped,
        record.deleted,
        record.embed_cache_hits,
        record.embed_cache_misses,
    )


def _run_from_row(row: tuple[Any, ...]) -> RunRecord:
    return RunRecord(
        at=str(row[0]),
        ok=bool(row[1]),
        error=str(row[2]),
        duration_seconds=float(row[3]),
        documents=int(row[4]),
        chunks=int(row[5]),
        changed=int(row[6]),
        skipped=int(row[7]),
        deleted=int(row[8]),
        embed_cache_hits=int(row[9]),
        embed_cache_misses=int(row[10]),
    )


def _decode_entry(value: object) -> DocEntry:
    if isinstance(value, str):
        return DocEntry(content=value)
    if isinstance(value, dict):
        content = value.get("content")
        listing = value.get("listing")
        return DocEntry(
            content=str(content) if content is not None else "",
            listing=str(listing) if isinstance(listing, str) else None,
        )
    return DocEntry(content=str(value))


__all__ = [
    "DocEntry",
    "DocStore",
    "JsonDocStore",
    "RunRecord",
    "SqliteDocStore",
]
