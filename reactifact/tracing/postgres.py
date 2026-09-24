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

from .columns import SpanArtifacts, TraceColumn, extract_columns
from .models import (
    AgentSpan,
    ArtifactRef,
    LLMCall,
    RelationRef,
    RunTrace,
    TagAssignment,
)
from .store import _SORT_COLUMNS, _like


class PostgresStore:
    """Postgres trace sink with async write + read. Requires the `pg` extra."""

    def __init__(self, dsn: str):
        from .._extras import require_extra

        self._psycopg = require_extra("PostgresStore", "psycopg", "pg")
        self.dsn = dsn
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
                        completion_tokens INTEGER NOT NULL DEFAULT 0
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
                        latency_ms REAL NOT NULL DEFAULT 0,
                        error TEXT,
                        started_at TIMESTAMPTZ,
                        reads JSONB NOT NULL DEFAULT '[]',
                        writes JSONB NOT NULL DEFAULT '[]',
                        relations JSONB NOT NULL DEFAULT '[]',
                        llm_calls JSONB NOT NULL DEFAULT '[]'
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
                    "ALTER TABLE spans ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ"
                )
            await conn.commit()
        finally:
            await conn.close()
        self._schema_ready = True

    async def export(self, trace: RunTrace) -> None:

        import psycopg.types.json

        await self._ensure_schema()
        started = trace.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        prompt_tokens = sum(c.prompt_tokens for c in trace.llm_calls)
        completion_tokens = sum(c.completion_tokens for c in trace.llm_calls)
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO runs (id, session_id, started_at, duration_ms, "
                    "outcome, prompt_tokens, completion_tokens) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
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
                    span_started = span.started_at
                    if span_started is not None and span_started.tzinfo is None:
                        span_started = span_started.replace(tzinfo=UTC)
                    await cur.execute(
                        "INSERT INTO spans (run_id, agent, event_type, latency_ms, error, "
                        "started_at, reads, writes, relations, llm_calls) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            trace.id,
                            span.agent,
                            span.event_type,
                            span.latency_ms,
                            span.error,
                            span_started,
                            psycopg.types.json.Jsonb(
                                [r.model_dump(mode="json") for r in span.reads]
                            ),
                            psycopg.types.json.Jsonb(
                                [w.model_dump(mode="json") for w in span.writes]
                            ),
                            psycopg.types.json.Jsonb(
                                [r.model_dump(mode="json") for r in span.relations]
                            ),
                            psycopg.types.json.Jsonb(
                                [c.model_dump(mode="json") for c in span.llm_calls]
                            ),
                        ),
                    )
            await conn.commit()
        finally:
            await conn.close()

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
            pattern = _like(q)
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
        sort_col = _SORT_COLUMNS.get(sort, "started_at")
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
                    placeholders = ",".join("%s" for _ in run_ids)
                    await cur.execute(
                        "SELECT run_id, agent, reads, writes FROM spans "
                        f"WHERE run_id IN ({placeholders}) ORDER BY id",
                        tuple(run_ids),
                    )
                    spans_by_run: dict[str, list[SpanArtifacts]] = {}
                    for run_id, agent, reads, writes in await cur.fetchall():
                        spans_by_run.setdefault(run_id, []).append(
                            SpanArtifacts(
                                agent=agent,
                                reads=[ArtifactRef(**d) for d in reads],
                                writes=[ArtifactRef(**d) for d in writes],
                            )
                        )
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
                            sph = ",".join("%s" for _ in ordered)
                            await cur.execute(
                                "SELECT run_id, agent, reads, writes FROM spans "
                                f"WHERE run_id IN ({sph}) ORDER BY id",
                                tuple(ordered),
                            )
                            by_run: dict[str, list[SpanArtifacts]] = {}
                            for run_id, agent, reads, writes in await cur.fetchall():
                                by_run.setdefault(run_id, []).append(
                                    SpanArtifacts(
                                        agent=agent,
                                        reads=[ArtifactRef(**d) for d in reads],
                                        writes=[ArtifactRef(**d) for d in writes],
                                    )
                                )
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
            }
            for r in rows
        ]
        return {"items": items, "total": total, "stats": stats}

    async def get(self, trace_id: str) -> RunTrace | None:
        await self._ensure_schema()
        conn = await self._psycopg.AsyncConnection.connect(self.dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, session_id, started_at, duration_ms, outcome "
                    "FROM runs WHERE id = %s",
                    (trace_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                await cur.execute(
                    "SELECT agent, event_type, latency_ms, error, started_at, "
                    "reads, writes, relations, llm_calls "
                    "FROM spans WHERE run_id = %s ORDER BY id",
                    (trace_id,),
                )
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

        spans = [
            AgentSpan(
                agent=r[0],
                event_type=r[1],
                latency_ms=r[2],
                error=r[3],
                started_at=r[4],
                reads=[ArtifactRef(**d) for d in r[5]],
                writes=[ArtifactRef(**d) for d in r[6]],
                relations=[RelationRef(**d) for d in r[7]],
                llm_calls=[LLMCall(**d) for d in r[8]],
            )
            for r in span_rows
        ]
        annotations = [
            TagAssignment(
                tag=a[0],
                note=a[1],
                color=a[2],
                created_at=a[3],
                updated_at=a[4],
            )
            for a in annotation_rows
        ]
        started = row[2]
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return RunTrace(
            id=row[0],
            session_id=row[1],
            started_at=started,
            duration_ms=row[3],
            outcome=row[4],
            spans=spans,
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
                    placeholders = ",".join("%s" for _ in run_ids)
                    await cur.execute(
                        "SELECT run_id, agent, reads, writes FROM spans "
                        f"WHERE run_id IN ({placeholders}) ORDER BY id",
                        tuple(run_ids),
                    )
                    out: dict[str, list[SpanArtifacts]] = {}
                    for rid, agent, reads, writes in await cur.fetchall():
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
            args.append(_like(q))
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
