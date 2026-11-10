# mypy: ignore-errors
"""PostgreSQL trace sink: pushes `RunTrace` to PG and can serve it back.

Requires the ``pg`` extra (psycopg async): imported lazily, so the `tracing`
package works without it. Schema mirrors the SQLite sink: `runs`/`spans` tables
with reads/writes/relations/llm_calls as jsonb, plus the `tags`/`run_tags`
annotation tables (reviewer labels + notes) — so the dashboard behaves the same
whichever backend it points at.

Both write (`export`) and read (`query`/`get`/annotation methods) are async —
this is what lets the web dashboard in `create_trace_router` read from Postgres
directly.

Connections are short-lived (opened per operation via
`psycopg.AsyncConnection`), so the store is not bound to any event loop and can
be shared across requests.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from ._shared import (
    SORT_COLUMNS,
    as_datetime,
    like,
    run_insert_values,
    span_from_row,
    span_insert_values,
    tag_assignment_from_row,
)
from .columns import SpanArtifacts, TraceColumn, extract_columns
from .models import (
    ArtifactRef,
    RunTrace,
)


class PostgresStore:
    """Postgres trace sink with async write + read. Requires the `pg` extra."""

    def __init__(
        self,
        dsn: str,
        *,
        max_runs: int | None = 200,
        column_scan_limit: int | None = 1000,
        max_session_runs: int = 50,
    ):
        from .._extras import require_extra

        self._psycopg = require_extra("PostgresStore", "psycopg", "pg")
        self.dsn = dsn
        self.max_runs = max_runs
        self.column_scan_limit = column_scan_limit
        self.max_session_runs = max_session_runs
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL DEFAULT '',
                        started_at TIMESTAMPTZ NOT NULL,
                        duration_ms REAL NOT NULL,
                        outcome TEXT NOT NULL,
                        prompt_tokens INTEGER NOT NULL DEFAULT 0,
                        completion_tokens INTEGER NOT NULL DEFAULT 0,
                        truncated INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS spans (
                        id BIGSERIAL PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES runs(id),
                        agent TEXT NOT NULL,
                        event_type TEXT NOT NULL DEFAULT '',
                        event_artifact_type TEXT NOT NULL DEFAULT '',
                        latency_ms REAL NOT NULL DEFAULT 0,
                        error TEXT,
                        started_at TIMESTAMPTZ,
                        reads JSONB NOT NULL DEFAULT '[]',
                        writes JSONB NOT NULL DEFAULT '[]',
                        relations JSONB NOT NULL DEFAULT '[]',
                        llm_calls JSONB NOT NULL DEFAULT '[]',
                        produces JSONB NOT NULL DEFAULT '[]',
                        tools JSONB NOT NULL DEFAULT '[]',
                        event_artifact_id TEXT NOT NULL DEFAULT '',
                        contract JSONB NOT NULL DEFAULT '{}'
                    )
                    """
                )
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tags (
                        name TEXT PRIMARY KEY,
                        color TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS run_tags (
                        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                        tag TEXT NOT NULL REFERENCES tags(name) ON DELETE CASCADE,
                        note TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (run_id, tag)
                    )
                    """
                )
                await cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_spans_run ON spans(run_id)",
                )
                await cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_run_tags_tag ON run_tags(tag)",
                )
                await cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_run_tags_run ON run_tags(run_id)",
                )
                # Old databases predate the token columns.
                await cur.execute(
                    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS "
                    "prompt_tokens INTEGER NOT NULL DEFAULT 0"
                )
                await cur.execute(
                    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS "
                    "completion_tokens INTEGER NOT NULL DEFAULT 0"
                )
                await cur.execute(
                    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS "
                    "truncated INTEGER NOT NULL DEFAULT 0"
                )
                await cur.execute(
                    "ALTER TABLE spans ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ"
                )
                await cur.execute(
                    "ALTER TABLE spans ADD COLUMN IF NOT EXISTS "
                    "produces JSONB NOT NULL DEFAULT '[]'"
                )
                await cur.execute(
                    "ALTER TABLE spans ADD COLUMN IF NOT EXISTS "
                    "event_artifact_type TEXT NOT NULL DEFAULT ''"
                )
                await cur.execute(
                    "ALTER TABLE spans ADD COLUMN IF NOT EXISTS "
                    "tools JSONB NOT NULL DEFAULT '[]'"
                )
                await cur.execute(
                    "ALTER TABLE spans ADD COLUMN IF NOT EXISTS "
                    "event_artifact_id TEXT NOT NULL DEFAULT ''"
                )
                await cur.execute(
                    "ALTER TABLE spans ADD COLUMN IF NOT EXISTS "
                    "contract JSONB NOT NULL DEFAULT '{}'"
                )
            await conn.commit()
        finally:
            await conn.close()
        self._schema_ready = True

    async def export(self, trace: RunTrace) -> None:

        import psycopg.types.json  # pyright: ignore[reportMissingImports]

        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO runs (id, session_id, started_at, duration_ms, "
                    "outcome, prompt_tokens, completion_tokens, truncated) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    run_insert_values(trace, timestamp=lambda moment: moment),
                )
                for span in trace.spans:
                    await cur.execute(
                        "INSERT INTO spans (run_id, agent, event_type, "
                        "event_artifact_type, latency_ms, error, started_at, reads, "
                        "writes, relations, llm_calls, produces, tools, "
                        "event_artifact_id, contract) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "%s, %s, %s)",
                        span_insert_values(
                            trace.id,
                            span,
                            timestamp=lambda moment: moment,
                            jsonify=lambda value: psycopg.types.json.Jsonb(value),
                        ),
                    )
                if self.max_runs is not None:
                    await self._prune_cur(cur, self.max_runs)
            await conn.commit()
        finally:
            await conn.close()

    async def _prune_cur(self, cur: Any, keep: int) -> int:
        await cur.execute(
            "SELECT id FROM runs ORDER BY started_at DESC, id DESC OFFSET %s",
            (keep,),
        )
        ids = [r[0] for r in await cur.fetchall()]
        if not ids:
            return 0
        placeholders = ",".join("%s" for _ in ids)
        await cur.execute(
            f"DELETE FROM spans WHERE run_id IN ({placeholders})", tuple(ids)
        )
        await cur.execute(
            f"DELETE FROM run_tags WHERE run_id IN ({placeholders})", tuple(ids)
        )
        await cur.execute(f"DELETE FROM runs WHERE id IN ({placeholders})", tuple(ids))
        return len(ids)

    async def prune(self, keep: int | None = None) -> int:
        keep = self.max_runs if keep is None else keep
        if keep is None:
            return 0
        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                removed = await self._prune_cur(cur, keep)
            await conn.commit()
        finally:
            await conn.close()
        return removed

    async def delete_run(self, trace_id: str) -> bool:
        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1 FROM runs WHERE id = %s", (trace_id,))
                if await cur.fetchone() is None:
                    return False
                await cur.execute("DELETE FROM spans WHERE run_id = %s", (trace_id,))
                await cur.execute("DELETE FROM run_tags WHERE run_id = %s", (trace_id,))
                await cur.execute("DELETE FROM runs WHERE id = %s", (trace_id,))
            await conn.commit()
        finally:
            await conn.close()
        return True

    async def delete_session(self, session_id: str) -> int:
        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM runs WHERE session_id = %s", (session_id,)
                )
                ids = [r[0] for r in await cur.fetchall()]
                if not ids:
                    return 0
                placeholders = ",".join("%s" for _ in ids)
                await cur.execute(
                    f"DELETE FROM spans WHERE run_id IN ({placeholders})", tuple(ids)
                )
                await cur.execute(
                    f"DELETE FROM run_tags WHERE run_id IN ({placeholders})", tuple(ids)
                )
                await cur.execute(
                    f"DELETE FROM runs WHERE id IN ({placeholders})", tuple(ids)
                )
            await conn.commit()
        finally:
            await conn.close()
        return len(ids)

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
            where.append("session_id = %s")
            args.append(session_id)
        if outcome is not None:
            where.append("outcome = %s")
            args.append(outcome)
        if tags:
            placeholders = ",".join("%s" for _ in tags)
            if tag_mode == "all":
                where.append(
                    "(SELECT COUNT(*) FROM run_tags rt WHERE rt.run_id = runs.id "
                    f"AND rt.tag IN ({placeholders})) = %s"
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
            pattern = like(q)
            where.append(
                "(runs.id LIKE %s ESCAPE '!' OR runs.session_id LIKE %s ESCAPE '!' "
                "OR runs.outcome LIKE %s ESCAPE '!' "
                "OR EXISTS (SELECT 1 FROM spans s WHERE s.run_id = runs.id "
                "AND (s.agent LIKE %s ESCAPE '!' OR s.error LIKE %s ESCAPE '!')))"
            )
            args.extend([pattern, pattern, pattern, pattern, pattern])
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        return where_sql, args

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
        columns: Sequence[TraceColumn] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        await self._ensure_schema()
        where_sql, args = self._where(session_id, outcome, tags, tag_mode, q)
        sort_col = SORT_COLUMNS.get(sort, "started_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"

        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(duration_ms), 0), "
                    f"COALESCE(SUM(prompt_tokens), 0), "
                    f"COALESCE(SUM(completion_tokens), 0) "
                    f"FROM runs{where_sql}",
                    tuple(args),
                )
                totals = await cur.fetchone()
                total = totals[0]
                stats = {
                    "runs": totals[0],
                    "duration_ms": round(totals[1], 1),
                    "prompt_tokens": totals[2],
                    "completion_tokens": totals[3],
                }
                await cur.execute(
                    f"SELECT id, session_id, started_at, duration_ms, outcome, "
                    f"prompt_tokens, completion_tokens, "
                    f"(SELECT COUNT(*) FROM spans WHERE run_id = runs.id) AS spans_count "
                    f"FROM runs{where_sql} ORDER BY {sort_col} {direction} "
                    f"LIMIT %s OFFSET %s",
                    (*args, limit, offset),
                )
                rows = await cur.fetchall()
                run_ids = [r[0] for r in rows]
                tags_by_run: dict[str, list[dict[str, Any]]] = {}
                if run_ids:
                    placeholders = ",".join("%s" for _ in run_ids)
                    await cur.execute(
                        "SELECT rt.run_id, rt.tag, rt.note, COALESCE(t.color, '') "
                        "FROM run_tags rt LEFT JOIN tags t ON t.name = rt.tag "
                        f"WHERE rt.run_id IN ({placeholders}) ORDER BY rt.created_at",
                        tuple(run_ids),
                    )
                    for run_id, tag, note, color in await cur.fetchall():
                        tags_by_run.setdefault(run_id, []).append(
                            {"tag": tag, "note": note, "color": color}
                        )
                fields_by_run: dict[str, dict[str, str]] = {}
                if run_ids and columns:

                    async def spans_of(
                        ids: list[str],
                    ) -> dict[str, list[SpanArtifacts]]:
                        out: dict[str, list[SpanArtifacts]] = {}
                        for rid in ids:
                            sql = (
                                "SELECT agent, reads, writes FROM spans "
                                "WHERE run_id = %s ORDER BY id"
                            )
                            args: tuple[Any, ...] = (rid,)
                            if self.column_scan_limit is not None:
                                sql += " LIMIT %s"
                                args = (rid, self.column_scan_limit)
                            await cur.execute(sql, args)
                            for agent, reads, writes in await cur.fetchall():
                                out.setdefault(rid, []).append(
                                    SpanArtifacts(
                                        agent=agent,
                                        reads=[ArtifactRef(**d) for d in reads],
                                        writes=[ArtifactRef(**d) for d in writes],
                                    )
                                )
                        return out

                    spans_by_run = await spans_of(run_ids)
                    needs_session = any(c.scope == "session" for c in columns)
                    session_order: dict[str, list[tuple[str, list[SpanArtifacts]]]] = {}
                    if needs_session:
                        sessions = sorted({r[1] for r in rows if r[1]})
                        for sid in sessions:
                            await cur.execute(
                                "SELECT id FROM runs WHERE session_id = %s "
                                "ORDER BY started_at ASC, id ASC",
                                (sid,),
                            )
                            ordered = [x[0] for x in await cur.fetchall()]
                            if not ordered:
                                continue
                            if len(ordered) > self.max_session_runs:
                                half = max(1, self.max_session_runs // 2)
                                ordered = ordered[:half] + ordered[-half:]
                            by_run = await spans_of(ordered)
                            session_order[sid] = [
                                (rid, by_run.get(rid, [])) for rid in ordered
                            ]
                    for row in rows:
                        session_spans: list[SpanArtifacts] | None = None
                        if needs_session:
                            ordered = session_order.get(row[1])
                            if ordered is not None:
                                session_spans = []
                                for rid, spans in ordered:
                                    session_spans.extend(spans)
                                    if rid == row[0]:
                                        break
                        fields_by_run[row[0]] = extract_columns(
                            spans_by_run.get(row[0], []),
                            columns,
                            session_spans=session_spans,
                        )
        finally:
            await conn.close()

        items = [
            {
                "id": r[0],
                "session_id": r[1],
                "started_at": r[2],
                "duration_ms": round(r[3], 1),
                "outcome": r[4],
                "prompt_tokens": r[5],
                "completion_tokens": r[6],
                "spans": r[7],
                "tags": tags_by_run.get(r[0], []),
                "fields": fields_by_run.get(r[0], {}),
                "fields_truncated": bool(
                    columns
                    and self.column_scan_limit is not None
                    and r[7] > self.column_scan_limit
                ),
            }
            for r in rows
        ]
        return {"items": items, "total": total, "stats": stats}

    async def get(
        self,
        trace_id: str,
        *,
        span_limit: int | None = 500,
        span_offset: int = 0,
    ) -> RunTrace | None:
        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, session_id, started_at, duration_ms, outcome, truncated "
                    "FROM runs WHERE id = %s",
                    (trace_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                await cur.execute(
                    "SELECT COUNT(*) FROM spans WHERE run_id = %s", (trace_id,)
                )
                span_total = (await cur.fetchone())[0]
                span_sql = (
                    "SELECT agent, event_type, event_artifact_type, latency_ms, error, "
                    "started_at, reads, writes, relations, llm_calls, produces, tools, "
                    "event_artifact_id, contract "
                    "FROM spans WHERE run_id = %s ORDER BY id"
                )
                span_args: tuple[Any, ...] = (trace_id,)
                if span_limit is not None:
                    span_sql += " LIMIT %s OFFSET %s"
                    span_args = (trace_id, span_limit, span_offset)
                await cur.execute(span_sql, span_args)
                span_rows = await cur.fetchall()
                await cur.execute(
                    "SELECT rt.tag, rt.note, COALESCE(t.color, ''), "
                    "rt.created_at, rt.updated_at "
                    "FROM run_tags rt LEFT JOIN tags t ON t.name = rt.tag "
                    "WHERE rt.run_id = %s ORDER BY rt.created_at",
                    (trace_id,),
                )
                annotation_rows = await cur.fetchall()
        finally:
            await conn.close()

        spans = [span_from_row(row) for row in span_rows]
        annotations = [tag_assignment_from_row(row) for row in annotation_rows]
        return RunTrace(
            id=row[0],
            session_id=row[1],
            started_at=as_datetime(row[2]) or datetime.now(UTC),
            duration_ms=row[3],
            outcome=row[4],
            spans=spans,
            span_total=span_total,
            span_offset=span_offset,
            has_more_spans=span_offset + len(spans) < span_total,
            trace_truncated=bool(row[5]),
            annotations=annotations,
        )

    async def field_values(
        self, run_id: str, columns: Sequence[TraceColumn]
    ) -> dict[str, str]:
        if not columns:
            return {}
        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT session_id FROM runs WHERE id = %s", (run_id,)
                )
                row = await cur.fetchone()
                if row is None:
                    return {}
                session_id = row[0]

                async def spans_of(
                    run_ids: list[str],
                ) -> dict[str, list[SpanArtifacts]]:
                    out: dict[str, list[SpanArtifacts]] = {}
                    for rid in run_ids:
                        sql = (
                            "SELECT agent, reads, writes FROM spans "
                            "WHERE run_id = %s ORDER BY id"
                        )
                        args: tuple[Any, ...] = (rid,)
                        if self.column_scan_limit is not None:
                            sql += " LIMIT %s"
                            args = (rid, self.column_scan_limit)
                        await cur.execute(sql, args)
                        for agent, reads, writes in await cur.fetchall():
                            out.setdefault(rid, []).append(
                                SpanArtifacts(
                                    agent=agent,
                                    reads=[ArtifactRef(**d) for d in reads],
                                    writes=[ArtifactRef(**d) for d in writes],
                                )
                            )
                    return out

                run_spans = (await spans_of([run_id])).get(run_id, [])
                session_spans: list[SpanArtifacts] | None = None
                if session_id and any(c.scope == "session" for c in columns):
                    await cur.execute(
                        "SELECT id FROM runs WHERE session_id = %s "
                        "ORDER BY started_at ASC, id ASC",
                        (session_id,),
                    )
                    ordered = [r[0] for r in await cur.fetchall()]
                    if ordered:
                        if len(ordered) > self.max_session_runs:
                            half = max(1, self.max_session_runs // 2)
                            ordered = ordered[:half] + ordered[-half:]
                        by_run = await spans_of(ordered)
                        session_spans = []
                        for rid in ordered:
                            session_spans.extend(by_run.get(rid, []))
                            if rid == run_id:
                                break
        finally:
            await conn.close()
        return extract_columns(run_spans, columns, session_spans=session_spans)

    async def sessions(
        self,
        *,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        await self._ensure_schema()
        where = " WHERE session_id != ''"
        args: list[Any] = []
        if q:
            where += " AND session_id LIKE %s ESCAPE '!'"
            args.append(like(q))
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT COUNT(DISTINCT session_id) FROM runs{where}",
                    tuple(args),
                )
                total = (await cur.fetchone())[0]
                await cur.execute(
                    "SELECT session_id, COUNT(*) AS runs, "
                    "COALESCE(SUM(duration_ms), 0), COALESCE(SUM(prompt_tokens), 0), "
                    "COALESCE(SUM(completion_tokens), 0), MAX(started_at), "
                    f"MIN(started_at) FROM runs{where} GROUP BY session_id "
                    "ORDER BY MAX(started_at) DESC LIMIT %s OFFSET %s",
                    (*args, limit, offset),
                )
                rows = await cur.fetchall()
        finally:
            await conn.close()
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

    async def list_tags(self) -> list[dict[str, Any]]:
        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT t.name, t.color, COUNT(rt.run_id)::int, t.created_at "
                    "FROM tags t LEFT JOIN run_tags rt ON rt.tag = t.name "
                    "GROUP BY t.name, t.color, t.created_at "
                    "ORDER BY COUNT(rt.run_id) DESC, t.name ASC"
                )
                rows = await cur.fetchall()
        finally:
            await conn.close()
        return [
            {
                "name": r[0],
                "color": r[1],
                "count": r[2],
                "created_at": (
                    r[3].isoformat() if isinstance(r[3], datetime) else str(r[3] or "")
                ),
            }
            for r in rows
        ]

    async def tag_runs(
        self,
        run_ids: list[str],
        tag: str,
        *,
        note: str = "",
        color: str = "",
    ) -> int:
        tag = tag.strip()
        if not tag or not run_ids:
            return 0
        await self._ensure_schema()
        now = datetime.now(UTC)
        placeholders = ",".join("%s" for _ in run_ids)
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                # Only real runs are annotated, so a typo'd id can never seed
                # the vocabulary with a tag that matches nothing.
                await cur.execute(
                    f"SELECT id FROM runs WHERE id IN ({placeholders})",
                    tuple(run_ids),
                )
                existing = [r[0] for r in await cur.fetchall()]
                if not existing:
                    return 0
                await cur.execute(
                    "INSERT INTO tags (name, color, created_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT(name) DO NOTHING",
                    (tag, color, now),
                )
                count = 0
                for run_id in existing:
                    await cur.execute(
                        "INSERT INTO run_tags "
                        "(run_id, tag, note, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT(run_id, tag) DO UPDATE SET "
                        "note = EXCLUDED.note, updated_at = EXCLUDED.updated_at",
                        (run_id, tag, note, now, now),
                    )
                    count += cur.rowcount
            await conn.commit()
        finally:
            await conn.close()
        return count

    async def untag_runs(self, run_ids: list[str], tag: str) -> int:
        if not run_ids:
            return 0
        await self._ensure_schema()
        placeholders = ",".join("%s" for _ in run_ids)
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"DELETE FROM run_tags WHERE tag = %s "
                    f"AND run_id IN ({placeholders})",
                    (tag, *run_ids),
                )
                count = cur.rowcount
            await conn.commit()
        finally:
            await conn.close()
        return count

    async def rename_tag(self, old: str, new: str) -> None:
        new = new.strip()
        if not new or old == new:
            return
        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO tags (name, color, created_at) "
                    "SELECT %s, color, created_at FROM tags WHERE name = %s "
                    "ON CONFLICT(name) DO NOTHING",
                    (new, old),
                )
                # A run may already carry both tags — drop the old assignment
                # there first so the update cannot violate the primary key.
                await cur.execute(
                    "DELETE FROM run_tags WHERE tag = %s AND run_id IN "
                    "(SELECT run_id FROM run_tags WHERE tag = %s)",
                    (old, new),
                )
                await cur.execute(
                    "UPDATE run_tags SET tag = %s WHERE tag = %s", (new, old)
                )
                await cur.execute("DELETE FROM tags WHERE name = %s", (old,))
            await conn.commit()
        finally:
            await conn.close()

    async def set_tag_color(self, name: str, color: str) -> None:
        await self._ensure_schema()
        now = datetime.now(UTC)
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO tags (name, color, created_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT(name) DO UPDATE SET color = EXCLUDED.color",
                    (name, color, now),
                )
            await conn.commit()
        finally:
            await conn.close()

    async def delete_tag(self, name: str) -> None:
        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM tags WHERE name = %s", (name,))
            await conn.commit()
        finally:
            await conn.close()
