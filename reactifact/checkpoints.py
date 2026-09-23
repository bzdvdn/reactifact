from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import sqlite3
import time
import urllib.parse
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, cast


class CheckpointBackend(ABC):
    """Interface for saving and loading the Workspace state (single blob)."""

    @abstractmethod
    async def save(self, data: dict[str, Any]) -> None:
        """Saves the state dictionary."""
        ...

    @abstractmethod
    async def load(self) -> dict[str, Any]:
        """Loads the state dictionary."""
        ...

    async def aclose(self) -> None:  # noqa: B027
        """Releases held resources (a pooled connection, ...). No-op by default."""


class KVBackend(ABC):
    """Key-value store. Used for sessions (session_id → Context)."""

    @abstractmethod
    async def set(self, key: str, data: dict[str, Any]) -> None: ...

    @abstractmethod
    async def get(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def keys(self) -> list[str]: ...

    async def aclose(self) -> None:  # noqa: B027
        """Releases held resources (a pooled connection, ...). No-op by default."""


class InMemoryKVBackend(KVBackend):
    """Dict-backed KV store — no files, no external service.

    The zero-config backend: ideal for tests, notebooks, and short-lived
    scripts where a session only needs to survive within one process. State
    is lost on exit (use `FileKVBackend`/`SQLiteKVBackend` to persist). Values
    are deep-copied on the way in and out so callers can never alias the
    stored state, matching the serialization boundary the file/SQL backends
    impose.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    async def set(self, key: str, data: dict[str, Any]) -> None:
        self._data[key] = copy.deepcopy(data)

    async def get(self, key: str) -> dict[str, Any] | None:
        data = self._data.get(key)
        return copy.deepcopy(data) if data is not None else None

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def keys(self) -> list[str]:
        return list(self._data)


class FileKVBackend(KVBackend):
    """File KV backend: one JSON file per key in a directory.

    Every call offloads its blocking filesystem I/O to a worker thread
    (`asyncio.to_thread`) so it never stalls the event loop reactifact's runtime
    and chat/web layers run on.
    """

    def __init__(self, directory: str):
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        safe = urllib.parse.quote(key, safe="")
        return self.directory / f"{safe}.json"

    def _set_sync(self, key: str, data: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        # json.dumps() + one write(), not json.dump(obj, f): CPython's
        # json.dump() streams via JSONEncoder.iterencode(), which — unlike
        # .encode() (what dumps() calls) — never takes the C-accelerated
        # encoder path, so it's the pure-Python encoder walking the whole
        # object graph node by node. For a large session (thousands of
        # artifacts/commits, written on every save — see reactifact/session.py)
        # that's a real, measured ~5-10x difference on identical content.
        payload = json.dumps({"key": key, "data": data})
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        tmp.replace(path)

    async def set(self, key: str, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._set_sync, key, data)

    def _get_sync(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return cast(dict[str, Any] | None, json.load(f).get("data"))

    async def get(self, key: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_sync, key)

    def _delete_sync(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete_sync, key)

    def _keys_sync(self) -> list[str]:
        self.directory.mkdir(parents=True, exist_ok=True)
        return [
            urllib.parse.unquote(p.stem) for p in sorted(self.directory.glob("*.json"))
        ]

    async def keys(self) -> list[str]:
        return await asyncio.to_thread(self._keys_sync)


class FileBackend(CheckpointBackend):
    """File backend: state is stored in a single JSON file."""

    def __init__(self, path: str):
        self.path = Path(path)

    def _save_sync(self, data: dict[str, Any]) -> None:
        # Atomic write (tmp + rename), same reasoning as `FileKVBackend._set_sync`:
        # a direct write left a truncated/corrupt file on a process kill mid-write
        # (OOM, deploy, kill -9) — the next `load()` would raise JSONDecodeError
        # and the session would be unrecoverable.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self.path)

    async def save(self, data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._save_sync, data)

    def _load_sync(self) -> dict[str, Any]:
        with open(self.path, encoding="utf-8") as f:
            return cast(dict[str, Any], json.load(f))

    async def load(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._load_sync)


class _AsyncSQLite:
    """One persistent `sqlite3.Connection` per backend instance, guarded by an
    `asyncio.Lock` and run off-thread via `asyncio.to_thread`.

    Reconnecting on every call (the previous behavior) meant every checkpoint
    write paid a fresh `connect()`, and concurrent writers had no `busy_timeout`
    to wait out a lock — they just raised `sqlite3.OperationalError: database is
    locked`. WAL mode lets readers and the single writer overlap; the lock here
    only serializes access from *this process* (SQLite itself still serializes
    writers across processes via the database file).
    """

    def __init__(self, db_path: str, init_sql: str):
        self.db_path = db_path
        self._init_sql = init_sql
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        # `busy_timeout` only takes effect once set on *this* connection, so
        # the pragmas below race the very connections that would honor it —
        # switching journal mode is an exclusive operation SQLite does not
        # always retry through the normal busy handler. Several backends
        # opening a brand-new file at once can still hit `database is
        # locked` here; retry the one-time bootstrap instead of the hot path.
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        attempts = 0
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute(self._init_sql)
                conn.commit()
                return conn
            except sqlite3.OperationalError:
                attempts += 1
                if attempts >= 10:
                    raise
                time.sleep(0.05 * attempts)

    def _exec(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        assert self._conn is not None
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur.fetchall()

    async def execute(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[tuple[Any, ...]]:
        async with self._lock:
            if self._conn is None:
                self._conn = await asyncio.to_thread(self._connect)
            return await asyncio.to_thread(self._exec, sql, params)

    async def aclose(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None


class SQLiteKVBackend(KVBackend):
    """SQLite KV backend: table kv_entries(key PRIMARY KEY, data JSON)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._sql = _AsyncSQLite(
            db_path,
            "CREATE TABLE IF NOT EXISTS kv_entries "
            "(key TEXT PRIMARY KEY, data TEXT NOT NULL)",
        )

    async def set(self, key: str, data: dict[str, Any]) -> None:
        await self._sql.execute(
            "INSERT OR REPLACE INTO kv_entries (key, data) VALUES (?, ?)",
            (key, json.dumps(data)),
        )

    async def get(self, key: str) -> dict[str, Any] | None:
        rows = await self._sql.execute(
            "SELECT data FROM kv_entries WHERE key = ?", (key,)
        )
        return cast(dict[str, Any], json.loads(rows[0][0])) if rows else None

    async def delete(self, key: str) -> None:
        await self._sql.execute("DELETE FROM kv_entries WHERE key = ?", (key,))

    async def keys(self) -> list[str]:
        rows = await self._sql.execute("SELECT key FROM kv_entries")
        return [cast(str, r[0]) for r in rows]

    async def aclose(self) -> None:
        await self._sql.aclose()


class SQLiteBackend(CheckpointBackend):
    """SQLite backend: state is stored in the checkpoint table (single row)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._sql = _AsyncSQLite(
            db_path,
            "CREATE TABLE IF NOT EXISTS checkpoint "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)",
        )

    async def save(self, data: dict[str, Any]) -> None:
        await self._sql.execute(
            "INSERT OR REPLACE INTO checkpoint (id, data) VALUES (1, ?)",
            (json.dumps(data),),
        )

    async def load(self) -> dict[str, Any]:
        rows = await self._sql.execute("SELECT data FROM checkpoint WHERE id = 1")
        if not rows:
            raise ValueError(f"Checkpoint not found in SQLite database: {self.db_path}")
        return cast(dict[str, Any], json.loads(rows[0][0]))

    async def aclose(self) -> None:
        await self._sql.aclose()


class PostgreSQLKVBackend(KVBackend):
    """PostgreSQL KV backend (`kv_entries(key, data)`) — behind the `pg` extra.

    Like the SQLite KV backend, but shared across processes: a natural store
    when sessions are persisted in the same Postgres as the application. The
    driver (`psycopg`) is imported lazily so the core stays dependency-free;
    install it with `uv sync --extra pg` (or group `pg`).

    Uses psycopg3's native async API (`AsyncConnection`) over one persistent,
    lazily-(re)connected connection, serialized by an `asyncio.Lock` — the same
    shape as `SQLiteKVBackend`. A dedicated connection per backend instance is
    enough for session-checkpoint traffic (small, infrequent writes); reach for
    `psycopg_pool.AsyncConnectionPool` yourself if you need many concurrent
    writers sharing one DSN.

    The connection **self-heals**: a query cancelled mid-flight (a client that
    drops a streaming turn) or any protocol error leaves the shared libpq
    connection busy/aborted, and a single poisoned connection would otherwise
    fail every later session read/write. Each statement reconnects and retries
    once, so one bad query cannot wedge the whole store.
    """

    def __init__(self, dsn: str):
        from ._extras import require_extra

        self._psycopg = require_extra("PostgreSQLKVBackend", "psycopg", "pg")
        self.dsn = dsn
        self._conn: Any | None = None
        self._lock = asyncio.Lock()

    async def _connection(self) -> Any:
        conn = self._conn
        if conn is None or conn.closed:
            conn = await self._psycopg.AsyncConnection.connect(self.dsn)
            async with conn.cursor() as cur:
                await cur.execute(
                    "CREATE TABLE IF NOT EXISTS kv_entries "
                    "(key TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )
            await conn.commit()
            self._conn = conn
        return conn

    async def _reset(self) -> None:
        """Drops the cached connection so the next statement reconnects.

        Called after any error: a connection that raised is never trusted again
        (it may be mid-command or in an aborted transaction).
        """
        conn, self._conn = self._conn, None
        if conn is not None and not conn.closed:
            with contextlib.suppress(Exception):
                await conn.close()

    async def _run(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        fetch: str | None = None,
    ) -> Any:
        """Runs one statement under the lock, reconnecting once on failure.

        All `kv_entries` statements are idempotent (upsert/delete/select), so a
        retry after a dropped connection is safe.
        """
        async with self._lock:
            for attempt in (0, 1):
                try:
                    conn = await self._connection()
                    async with conn.cursor() as cur:
                        await cur.execute(sql, params)
                        result = await getattr(cur, fetch)() if fetch else None
                    await conn.commit()
                    return result
                except self._psycopg.Error:
                    await self._reset()
                    if attempt:
                        raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def set(self, key: str, data: dict[str, Any]) -> None:
        await self._run(
            "INSERT INTO kv_entries (key, data) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data",
            (key, json.dumps(data)),
        )

    async def get(self, key: str) -> dict[str, Any] | None:
        row = await self._run(
            "SELECT data FROM kv_entries WHERE key = %s", (key,), fetch="fetchone"
        )
        return cast(dict[str, Any], json.loads(row[0])) if row is not None else None

    async def delete(self, key: str) -> None:
        await self._run("DELETE FROM kv_entries WHERE key = %s", (key,))

    async def keys(self) -> list[str]:
        rows = await self._run("SELECT key FROM kv_entries", fetch="fetchall")
        return [cast(str, r[0]) for r in rows]

    async def aclose(self) -> None:
        async with self._lock:
            await self._reset()
