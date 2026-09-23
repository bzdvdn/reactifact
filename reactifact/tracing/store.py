"""Trace sinks: what accepts a finished `RunTrace`.

`TraceStore` is the SQLite sink (`runs`/`spans`/`tags`/`run_tags` tables),
`PostgresStore` the Postgres one. Both expose an async interface
(`export`/`query`/`get`, plus the annotation methods) — SQLite keeps its sync
`sqlite3` core and bridges it with `asyncio.to_thread`, so the whole tracing
surface is uniform `async` (this is what makes a web dashboard over Postgres
possible). Langfuse is a separate `Tracer` subclass.

Tags are **reviewer annotations** (§54): the runtime never writes them; the
dashboard attaches them to a run *after* inspecting it, so runs can later be
found by tag and acted on (prompt/behaviour fixes). They live in two tables —
`tags` (the managed vocabulary: name + colour) and `run_tags` (assignments:
run, tag, note, timestamps) — mirrored by both backends.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .models import (
    AgentSpan,
    ArtifactRef,
    LLMCall,
    RelationRef,
    RunTrace,
    TagAssignment,
)

#: Sort keys the dashboard may request; anything else falls back to the default.
_SORT_COLUMNS = {
    "started_at": "started_at",
    "duration_ms": "duration_ms",
    "outcome": "outcome",
    "session_id": "session_id",
    "spans": "spans_count",
}


def _like(query: str) -> str:
    """Escapes a user search into a `LIKE` pattern (paired with `ESCAPE '!'`).

    `!` rather than the usual backslash keeps the pattern valid on Postgres too,
    where a lone trailing backslash inside a string literal is awkward.
    """
    escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _iso(value: float | datetime | None) -> str:
    """Normalizes a stored timestamp to ISO-8601 for the dashboard JSON."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    else:
        moment = datetime.fromtimestamp(value, tz=UTC)
    return moment.isoformat()


class TraceSink(Protocol):
    """Async interface for trace sinks (SQLite, Postgres, Langfuse…)."""

    async def export(self, trace: RunTrace) -> None: ...


class TraceReader(Protocol):
    """Async read interface used by the web dashboard (§54)."""

    async def query(
        self,
        *,
        session_id: str | None = None,
        outcome: str | None = None,
        tags: list[str] | None = None,
        tag_mode: str = "any",
        q: str | None = None,
        sort: str = "started_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]: ...

    async def get(self, trace_id: str) -> RunTrace | None: ...

    async def sessions(
        self,
        *,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]: ...


class TraceAnnotator(Protocol):
    """Async annotation interface: the reviewer workflow the dashboard drives."""

    async def list_tags(self) -> list[dict[str, Any]]: ...

    async def tag_runs(
        self,
        run_ids: list[str],
        tag: str,
        *,
        note: str = "",
        color: str = "",
    ) -> int: ...

    async def untag_runs(self, run_ids: list[str], tag: str) -> int: ...

    async def rename_tag(self, old: str, new: str) -> None: ...

    async def set_tag_color(self, name: str, color: str) -> None: ...

    async def delete_tag(self, name: str) -> None: ...


class TraceStoreProtocol(TraceReader, TraceAnnotator, Protocol):
    """What `create_trace_router` needs: read + annotate."""


class TraceStore:
    """SQLite trace sink: writes `RunTrace` and can serve them back.

    The `sqlite3` connection is used from a single worker thread: public
    methods are async and delegate to sync implementations via
    `asyncio.to_thread`. Pass `bridge=None` only inside tests that drive the
    sync core directly.
    """

    def __init__(
        self,
        path: str = "traces.db",
        *,
        timeout: float = 10.0,
        max_runs: int | None = 200,
    ):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.max_runs = max_runs
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=timeout)
        self._create_schema()
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL DEFAULT '',
                started_at REAL NOT NULL,
                duration_ms REAL NOT NULL,
                outcome TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS spans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(id),
                agent TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT '',
                latency_ms REAL NOT NULL DEFAULT 0,
                error TEXT,
                started_at REAL,
                reads TEXT NOT NULL DEFAULT '[]',
                writes TEXT NOT NULL DEFAULT '[]',
                relations TEXT NOT NULL DEFAULT '[]',
                llm_calls TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS tags (
                name TEXT PRIMARY KEY,
                color TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_tags (
                run_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (run_id, tag)
            );
            CREATE INDEX IF NOT EXISTS idx_spans_run ON spans(run_id);
            CREATE INDEX IF NOT EXISTS idx_run_tags_tag ON run_tags(tag);
            CREATE INDEX IF NOT EXISTS idx_run_tags_run ON run_tags(run_id);
            """
        )
        self._conn.commit()

    def _migrate(self) -> None:
        """Adds columns that appeared after the old schema (like teff)."""
        try:
            cols = {row[1] for row in self._conn.execute("PRAGMA table_info(spans)")}
        except sqlite3.OperationalError:
            return
        if "llm_calls" not in cols:
            self._conn.execute(
                "ALTER TABLE spans ADD COLUMN llm_calls TEXT NOT NULL DEFAULT '[]'"
            )
        if "relations" not in cols:
            self._conn.execute(
                "ALTER TABLE spans ADD COLUMN relations TEXT NOT NULL DEFAULT '[]'"
            )
        if "started_at" not in cols:
            self._conn.execute("ALTER TABLE spans ADD COLUMN started_at REAL")
        run_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(runs)")}
        if "prompt_tokens" not in run_cols:
            self._conn.execute(
                "ALTER TABLE runs ADD COLUMN prompt_tokens INTEGER NOT NULL DEFAULT 0"
            )
        if "completion_tokens" not in run_cols:
            self._conn.execute(
                "ALTER TABLE runs ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0"
            )
        self._conn.commit()

    # ---- async public API (bridged to the sync core) ----------------------- #

    async def export(self, trace: RunTrace) -> None:
        await asyncio.to_thread(self._export_sync, trace)

    async def prune(self, keep: int) -> int:
        return await asyncio.to_thread(self._prune_sync, keep)

    async def query(
        self,
        *,
        session_id: str | None = None,
        outcome: str | None = None,
        tags: list[str] | None = None,
        tag_mode: str = "any",
        q: str | None = None,
        sort: str = "started_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._query_sync,
            session_id,
            outcome,
            tags,
            tag_mode,
            q,
            sort,
            order,
            limit,
            offset,
        )

    async def get(self, trace_id: str) -> RunTrace | None:
        return await asyncio.to_thread(self._get_sync, trace_id)

    async def sessions(
        self,
        *,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._sessions_sync, q, limit, offset)

    async def list_tags(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_tags_sync)

    async def tag_runs(
        self,
        run_ids: list[str],
        tag: str,
        *,
        note: str = "",
        color: str = "",
    ) -> int:
        return await asyncio.to_thread(self._tag_runs_sync, run_ids, tag, note, color)

    async def untag_runs(self, run_ids: list[str], tag: str) -> int:
        return await asyncio.to_thread(self._untag_runs_sync, run_ids, tag)

    async def rename_tag(self, old: str, new: str) -> None:
        await asyncio.to_thread(self._rename_tag_sync, old, new)

    async def set_tag_color(self, name: str, color: str) -> None:
        await asyncio.to_thread(self._set_tag_color_sync, name, color)

    async def delete_tag(self, name: str) -> None:
        await asyncio.to_thread(self._delete_tag_sync, name)

    # ---- sync core --------------------------------------------------------- #

    def _export_sync(self, trace: RunTrace) -> None:
        started = trace.started_at.timestamp() if trace.started_at else time.time()
        prompt_tokens = sum(c.prompt_tokens for c in trace.llm_calls)
        completion_tokens = sum(c.completion_tokens for c in trace.llm_calls)
        self._conn.execute(
            "INSERT INTO runs (id, session_id, started_at, duration_ms, outcome, "
            "prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                trace.id,
                trace.session_id,
                started,
                trace.duration_ms,
                trace.outcome,
                prompt_tokens,
                completion_tokens,
            ),
        )
        for span in trace.spans:
            self._conn.execute(
                "INSERT INTO spans (run_id, agent, event_type, latency_ms, error, "
                "started_at, reads, writes, relations, llm_calls) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace.id,
                    span.agent,
                    span.event_type,
                    span.latency_ms,
                    span.error,
                    span.started_at.timestamp() if span.started_at else None,
                    json.dumps([r.model_dump(mode="json") for r in span.reads]),
                    json.dumps([w.model_dump(mode="json") for w in span.writes]),
                    json.dumps([r.model_dump(mode="json") for r in span.relations]),
                    json.dumps([c.model_dump(mode="json") for c in span.llm_calls]),
                ),
            )
        self._conn.commit()
        if self.max_runs is not None:
            self._prune_sync(self.max_runs)

    def _prune_sync(self, keep: int) -> int:
        """Keeps the last `keep` traces, deletes older ones (retention)."""
        cur = self._conn.execute(
            "DELETE FROM runs WHERE id NOT IN "
            "(SELECT id FROM runs ORDER BY started_at DESC LIMIT ?)",
            (keep,),
        )
        self._conn.execute(
            "DELETE FROM spans WHERE run_id NOT IN (SELECT id FROM runs)"
        )
        self._conn.execute(
            "DELETE FROM run_tags WHERE run_id NOT IN (SELECT id FROM runs)"
        )
        self._conn.commit()
        return cur.rowcount

    def _where(
        self,
        session_id: str | None,
        outcome: str | None,
        tags: list[str] | None,
        tag_mode: str,
        q: str | None,
    ) -> tuple[str, list[Any]]:
        where: list[str] = []
        args: list[Any] = []
        if session_id is not None:
            where.append("session_id = ?")
            args.append(session_id)
        if outcome is not None:
            where.append("outcome = ?")
            args.append(outcome)
        if tags:
            placeholders = ",".join("?" for _ in tags)
            if tag_mode == "all":
                where.append(
                    "(SELECT COUNT(*) FROM run_tags rt WHERE rt.run_id = runs.id "
                    f"AND rt.tag IN ({placeholders})) = ?"
                )
                args.extend(tags)
                args.append(len(tags))
            else:
                where.append(
                    "EXISTS (SELECT 1 FROM run_tags rt WHERE rt.run_id = runs.id "
                    f"AND rt.tag IN ({placeholders}))"
                )
                args.extend(tags)
        if q:
            pattern = _like(q)
            where.append(
                "(runs.id LIKE ? ESCAPE '!' OR runs.session_id LIKE ? ESCAPE '!' "
                "OR runs.outcome LIKE ? ESCAPE '!' "
                "OR EXISTS (SELECT 1 FROM spans s WHERE s.run_id = runs.id "
                "AND (s.agent LIKE ? ESCAPE '!' OR s.error LIKE ? ESCAPE '!')))"
            )
            args.extend([pattern, pattern, pattern, pattern, pattern])
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        return where_sql, args

    def _tags_by_run(self, run_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not run_ids:
            return {}
        placeholders = ",".join("?" for _ in run_ids)
        rows = self._conn.execute(
            "SELECT rt.run_id, rt.tag, rt.note, COALESCE(t.color, '') "
            f"FROM run_tags rt LEFT JOIN tags t ON t.name = rt.tag "
            f"WHERE rt.run_id IN ({placeholders}) ORDER BY rt.created_at",
            tuple(run_ids),
        ).fetchall()
        out: dict[str, list[dict[str, Any]]] = {}
        for run_id, tag, note, color in rows:
            out.setdefault(run_id, []).append(
                {"tag": tag, "note": note, "color": color}
            )
        return out

    def _query_sync(
        self,
        session_id: str | None,
        outcome: str | None,
        tags: list[str] | None,
        tag_mode: str,
        q: str | None,
        sort: str,
        order: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        """Latest traces with filters and pagination (§54).

        Returns ``{"items": [...], "total": n}``, where total is before pagination.
        """
        where_sql, args = self._where(session_id, outcome, tags, tag_mode, q)
        sort_col = _SORT_COLUMNS.get(sort, "started_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"

        totals = self._conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(duration_ms), 0), "
            f"COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) "
            f"FROM runs{where_sql}",
            tuple(args),
        ).fetchone()
        total = totals[0]
        stats = {
            "runs": totals[0],
            "duration_ms": round(totals[1], 1),
            "prompt_tokens": totals[2],
            "completion_tokens": totals[3],
        }
        rows = self._conn.execute(
            f"SELECT id, session_id, started_at, duration_ms, outcome, "
            f"prompt_tokens, completion_tokens, "
            f"(SELECT COUNT(*) FROM spans WHERE run_id = runs.id) AS spans_count "
            f"FROM runs{where_sql} ORDER BY {sort_col} {direction} LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        tags_by_run = self._tags_by_run([row[0] for row in rows])
        items = [
            {
                "id": row[0],
                "session_id": row[1],
                "started_at": row[2],
                "duration_ms": round(row[3], 1),
                "outcome": row[4],
                "prompt_tokens": row[5],
                "completion_tokens": row[6],
                "spans": row[7],
                "tags": tags_by_run.get(row[0], []),
            }
            for row in rows
        ]
        return {"items": items, "total": total, "stats": stats}

    def _get_sync(self, trace_id: str) -> RunTrace | None:
        row = self._conn.execute(
            "SELECT id, session_id, started_at, duration_ms, outcome "
            "FROM runs WHERE id = ?",
            (trace_id,),
        ).fetchone()
        if row is None:
            return None
        span_rows = self._conn.execute(
            "SELECT agent, event_type, latency_ms, error, started_at, "
            "reads, writes, relations, llm_calls "
            "FROM spans WHERE run_id = ? ORDER BY id",
            (trace_id,),
        ).fetchall()
        spans = [
            AgentSpan(
                agent=r[0],
                event_type=r[1],
                latency_ms=r[2],
                error=r[3],
                started_at=(
                    datetime.fromtimestamp(r[4], tz=UTC) if r[4] is not None else None
                ),
                reads=[ArtifactRef(**d) for d in json.loads(r[5])],
                writes=[ArtifactRef(**d) for d in json.loads(r[6])],
                relations=[RelationRef(**d) for d in json.loads(r[7])],
                llm_calls=[LLMCall(**d) for d in json.loads(r[8])],
            )
            for r in span_rows
        ]
        annotation_rows = self._conn.execute(
            "SELECT rt.tag, rt.note, COALESCE(t.color, ''), rt.created_at, rt.updated_at "
            "FROM run_tags rt LEFT JOIN tags t ON t.name = rt.tag "
            "WHERE rt.run_id = ? ORDER BY rt.created_at",
            (trace_id,),
        ).fetchall()
        annotations = [
            TagAssignment(
                tag=a[0],
                note=a[1],
                color=a[2],
                created_at=datetime.fromtimestamp(a[3], tz=UTC),
                updated_at=datetime.fromtimestamp(a[4], tz=UTC),
            )
            for a in annotation_rows
        ]
        return RunTrace(
            id=row[0],
            session_id=row[1],
            started_at=datetime.fromtimestamp(row[2], tz=UTC),
            duration_ms=row[3],
            outcome=row[4],
            spans=spans,
            annotations=annotations,
        )

    def _sessions_sync(self, q: str | None, limit: int, offset: int) -> dict[str, Any]:
        """Sessions with aggregates: one row per non-empty `session_id`."""
        where = " WHERE session_id != ''"
        args: list[Any] = []
        if q:
            where += " AND session_id LIKE ? ESCAPE '!'"
            args.append(_like(q))
        total = self._conn.execute(
            f"SELECT COUNT(DISTINCT session_id) FROM runs{where}", tuple(args)
        ).fetchone()[0]
        rows = self._conn.execute(
            "SELECT session_id, COUNT(*) AS runs, "
            "COALESCE(SUM(duration_ms), 0), COALESCE(SUM(prompt_tokens), 0), "
            "COALESCE(SUM(completion_tokens), 0), MAX(started_at), MIN(started_at) "
            f"FROM runs{where} GROUP BY session_id "
            "ORDER BY MAX(started_at) DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        items = [
            {
                "session_id": r[0],
                "runs": r[1],
                "duration_ms": round(r[2], 1),
                "prompt_tokens": r[3],
                "completion_tokens": r[4],
                "last_at": r[5],
                "first_at": r[6],
            }
            for r in rows
        ]
        return {"items": items, "total": total}

    # ---- annotation core --------------------------------------------------- #

    def _list_tags_sync(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT t.name, t.color, COUNT(rt.run_id) AS c, t.created_at "
            "FROM tags t LEFT JOIN run_tags rt ON rt.tag = t.name "
            "GROUP BY t.name, t.color, t.created_at "
            "ORDER BY c DESC, t.name ASC"
        ).fetchall()
        return [
            {"name": r[0], "color": r[1], "count": r[2], "created_at": _iso(r[3])}
            for r in rows
        ]

    def _tag_runs_sync(
        self, run_ids: list[str], tag: str, note: str, color: str
    ) -> int:
        tag = tag.strip()
        if not tag or not run_ids:
            return 0
        # Only real runs are annotated, so a typo'd id can never seed the
        # vocabulary with a tag that matches nothing.
        existing = [
            run_id
            for run_id in run_ids
            if self._conn.execute(
                "SELECT 1 FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            is not None
        ]
        if not existing:
            return 0
        now = time.time()
        self._conn.execute(
            "INSERT INTO tags (name, color, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (tag, color, now),
        )
        count = 0
        for run_id in existing:
            cur = self._conn.execute(
                "INSERT INTO run_tags (run_id, tag, note, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, tag) DO UPDATE SET "
                "note = excluded.note, updated_at = excluded.updated_at",
                (run_id, tag, note, now, now),
            )
            count += cur.rowcount
        self._conn.commit()
        return count

    def _untag_runs_sync(self, run_ids: list[str], tag: str) -> int:
        if not run_ids:
            return 0
        placeholders = ",".join("?" for _ in run_ids)
        cur = self._conn.execute(
            f"DELETE FROM run_tags WHERE tag = ? AND run_id IN ({placeholders})",
            (tag, *run_ids),
        )
        self._conn.commit()
        return cur.rowcount

    def _rename_tag_sync(self, old: str, new: str) -> None:
        new = new.strip()
        if not new or old == new:
            return
        self._conn.execute(
            "INSERT INTO tags (name, color, created_at) "
            "SELECT ?, color, created_at FROM tags WHERE name = ? "
            "ON CONFLICT(name) DO NOTHING",
            (new, old),
        )
        self._conn.execute(
            "UPDATE OR REPLACE run_tags SET tag = ? WHERE tag = ?", (new, old)
        )
        self._conn.execute("DELETE FROM tags WHERE name = ?", (old,))
        self._conn.commit()

    def _set_tag_color_sync(self, name: str, color: str) -> None:
        self._conn.execute(
            "INSERT INTO tags (name, color, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET color = excluded.color",
            (name, color, time.time()),
        )
        self._conn.commit()

    def _delete_tag_sync(self, name: str) -> None:
        self._conn.execute("DELETE FROM run_tags WHERE tag = ?", (name,))
        self._conn.execute("DELETE FROM tags WHERE name = ?", (name,))
        self._conn.commit()
