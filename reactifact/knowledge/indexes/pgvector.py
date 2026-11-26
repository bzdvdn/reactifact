"""PostgreSQL/pgvector index — optional production vector storage.

Requires the ``pg`` extra (``pip install "reactifact[pg]"``) and a database
with the ``vector`` extension available. The driver is imported lazily, so
`reactifact.knowledge` stays importable without psycopg.

The adapter uses one lazily-opened, autocommit connection (serialized by an
`asyncio.Lock`) and deterministic point IDs, so re-runs overwrite instead of
duplicating. `keyword_search` uses Postgres full-text ranking, which makes
hybrid retrieval fully server-side.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

from ..._extras import require_extra
from ..errors import ConfigError
from ..models import Chunk, SearchHit
from .base import check_dimensions, chunk_point_id

_UPSERT_BATCH_SIZE = 500


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


class PgVectorIndex:
    """Stores chunk vectors in a pgvector column (requires the ``pg`` extra).

    `dsn` is a psycopg connection string; alternatively `dsn_env` names an
    environment variable holding it. The table is created on demand and the
    embedding dimension is inferred from the first upsert. `text_search_config`
    is the Postgres text-search configuration used by the GIN index and
    `keyword_search` (``simple`` works across languages; ``english`` adds
    stemming).
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        dsn_env: str | None = None,
        table: str = "reactifact_chunks",
        dimensions: int | None = None,
        text_search_config: str = "simple",
    ) -> None:
        if dsn and dsn_env:
            raise ValueError("provide either dsn or dsn_env, not both")
        if dsn_env:
            dsn = os.environ.get(dsn_env)
            if not dsn:
                raise ValueError(f"environment variable {dsn_env!r} is not set")
        if not dsn:
            raise ValueError("pgvector index requires a 'dsn' (or 'dsn_env')")
        self.dsn = dsn
        self.table = table
        self.text_search_config = text_search_config
        self._dimensions = dimensions
        self._ready = False
        self._connection: Any | None = None
        self._lock = asyncio.Lock()
        self._psycopg = require_extra("PgVectorIndex", "psycopg", "pg")
        self._sql = require_extra("PgVectorIndex", "psycopg.sql", "pg")

    @property
    def signature(self) -> str:
        """Pipeline fingerprint for this table and DSN."""
        digest = hashlib.sha256(self.dsn.encode("utf-8")).hexdigest()[:12]
        return f"pgvector:{self.table}:{digest}"

    async def _connect(self) -> Any:
        return await self._psycopg.AsyncConnection.connect(self.dsn, autocommit=True)

    async def _get_connection(self) -> Any:
        async with self._lock:
            if self._connection is None or self._connection.closed:
                self._connection = await self._connect()
            return self._connection

    async def aclose(self) -> None:
        """Close the Postgres connection."""
        async with self._lock:
            if self._connection is not None and not self._connection.closed:
                await self._connection.close()
            self._connection = None

    def _qualified_table(self) -> Any:
        return self._sql.Identifier(self.table)

    async def _ensure_table(self, dimensions: int) -> None:
        if self._dimensions is None:
            self._dimensions = dimensions
        elif self._dimensions != dimensions:
            raise ConfigError(
                f"pgvector table {self.table!r} is {self._dimensions}-"
                f"dimensional, but a {dimensions}-dimensional vector arrived"
            )
        if self._ready:
            return
        connection = await self._get_connection()
        await connection.execute(self._sql.SQL("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(
            self._sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} ("
                "id text PRIMARY KEY, "
                "source_uri text NOT NULL, "
                "text text NOT NULL, "
                "source text NOT NULL, "
                "artifact_hash text NOT NULL, "
                "embedding vector({}) NOT NULL, "
                "metadata jsonb NOT NULL DEFAULT '{{}}'"
                ")"
            ).format(
                self._qualified_table(),
                self._sql.Literal(dimensions),
            )
        )
        config = self._sql.Literal(self.text_search_config)
        await connection.execute(
            self._sql.SQL(
                "CREATE INDEX IF NOT EXISTS {} ON {} USING GIN (to_tsvector({}, text))"
            ).format(
                self._sql.Identifier(f"{self.table}_text_fts"),
                self._qualified_table(),
                config,
            )
        )
        self._ready = True

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
        """Insert or replace one point."""
        await self.upsert_batch([(chunk, vector, artifact_hash)], source_id=source_id)

    async def upsert_batch(
        self,
        items: Sequence[tuple[Chunk, list[float], str]],
        *,
        source_id: str,
    ) -> None:
        """Insert or replace a batch of points."""
        if not items:
            return
        await self._ensure_table(len(items[0][1]))
        for _, vector, _ in items:
            check_dimensions(self._dimensions, vector, backend="pgvector")
        connection = await self._get_connection()
        for start in range(0, len(items), _UPSERT_BATCH_SIZE):
            batch = items[start : start + _UPSERT_BATCH_SIZE]
            placeholders = ", ".join(
                ["(%s, %s, %s, %s, %s, %s::vector, %s::jsonb)"] * len(batch)
            )
            params: list[object] = []
            for chunk, vector, artifact_hash in batch:
                params.extend(
                    (
                        str(chunk_point_id(chunk)),
                        chunk.source_uri,
                        chunk.text,
                        source_id,
                        artifact_hash,
                        _vector_literal(vector),
                        json.dumps(
                            chunk.metadata,
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                )
            statement = self._sql.SQL(
                "INSERT INTO {} "
                "(id, source_uri, text, source, artifact_hash, embedding, metadata) "
                "VALUES " + placeholders + " "
                "ON CONFLICT (id) DO UPDATE SET "
                "source_uri = EXCLUDED.source_uri, "
                "text = EXCLUDED.text, "
                "source = EXCLUDED.source, "
                "artifact_hash = EXCLUDED.artifact_hash, "
                "embedding = EXCLUDED.embedding, "
                "metadata = EXCLUDED.metadata"
            ).format(self._qualified_table())
            await connection.execute(statement, params)

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        if self._dimensions is None:
            return
        await self._ensure_table(self._dimensions)
        connection = await self._get_connection()
        if current:
            statement = self._sql.SQL(
                "DELETE FROM {} WHERE source = %s AND "
                "(source_uri, artifact_hash) NOT IN "
                "(SELECT * FROM unnest(%s::text[], %s::text[]))"
            ).format(self._qualified_table())
            params: tuple[object, ...] = (
                source_id,
                list(current.keys()),
                [current[key] for key in current],
            )
        else:
            statement = self._sql.SQL("DELETE FROM {} WHERE source = %s").format(
                self._qualified_table()
            )
            params = (source_id,)
        await connection.execute(statement, params)

    async def count(self) -> int:
        """Number of stored points."""
        if self._dimensions is None:
            return 0
        await self._ensure_table(self._dimensions)
        connection = await self._get_connection()
        cursor = await connection.execute(
            self._sql.SQL("SELECT count(*) FROM {}").format(self._qualified_table())
        )
        row = await cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
        source_id: str | None = None,
    ) -> list[SearchHit]:
        """Dense similarity search with SQL-level filters."""
        if self._dimensions is None:
            return []
        check_dimensions(self._dimensions, vector, backend="pgvector")
        await self._ensure_table(self._dimensions)
        literal = _vector_literal(vector)
        conditions: list[Any] = []
        params: list[object] = [literal]
        if filters:
            conditions.append(self._sql.SQL("metadata @> %s::jsonb"))
            params.append(json.dumps(dict(filters)))
        if source_id is not None:
            conditions.append(self._sql.SQL("source = %s"))
            params.append(source_id)
        if principals is not None:
            conditions.append(
                self._sql.SQL(
                    "(NOT (metadata ? 'acl') "
                    "OR jsonb_array_length(metadata->'acl') = 0 "
                    "OR metadata->'acl' ?| %s::text[])"
                )
            )
            params.append(list(principals))
        where = self._sql.SQL("")
        if conditions:
            where = self._sql.SQL("WHERE {}").format(
                self._sql.SQL(" AND ").join(conditions)
            )
        statement = self._sql.SQL(
            "SELECT text, source_uri, metadata, "
            "1 - (embedding <=> %s::vector) AS score "
            "FROM {} {} ORDER BY embedding <=> %s::vector LIMIT %s"
        ).format(self._qualified_table(), where)
        connection = await self._get_connection()
        cursor = await connection.execute(statement, (*params, literal, top_k))
        rows = await cursor.fetchall()
        return [
            SearchHit(
                text=row[0],
                source_uri=row[1],
                metadata=dict(row[2]),
                score=float(row[3]),
            )
            for row in rows
        ]

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
        source_id: str | None = None,
    ) -> list[SearchHit]:
        """Postgres full-text search over stored chunks."""
        if self._dimensions is None:
            return []
        await self._ensure_table(self._dimensions)
        config = self._sql.Literal(self.text_search_config)
        conditions: list[Any] = [
            self._sql.SQL("to_tsvector({}, text) @@ plainto_tsquery({}, %s)").format(
                config, config
            )
        ]
        select_params: list[object] = [text]
        where_params: list[object] = [text]
        if filters:
            conditions.append(self._sql.SQL("metadata @> %s::jsonb"))
            where_params.append(json.dumps(dict(filters)))
        if source_id is not None:
            conditions.append(self._sql.SQL("source = %s"))
            where_params.append(source_id)
        if principals is not None:
            conditions.append(
                self._sql.SQL(
                    "(NOT (metadata ? 'acl') "
                    "OR jsonb_array_length(metadata->'acl') = 0 "
                    "OR metadata->'acl' ?| %s::text[])"
                )
            )
            where_params.append(list(principals))
        where = self._sql.SQL(" AND ").join(conditions)
        statement = self._sql.SQL(
            "SELECT text, source_uri, metadata, "
            "ts_rank(to_tsvector({}, text), "
            "plainto_tsquery({}, %s)) AS score "
            "FROM {} WHERE {} ORDER BY score DESC LIMIT %s"
        ).format(config, config, self._qualified_table(), where)
        connection = await self._get_connection()
        cursor = await connection.execute(
            statement, (*select_params, *where_params, top_k)
        )
        rows = await cursor.fetchall()
        return [
            SearchHit(
                text=row[0],
                source_uri=row[1],
                metadata=dict(row[2]),
                score=float(row[3]),
            )
            for row in rows
        ]


__all__ = ["PgVectorIndex"]
