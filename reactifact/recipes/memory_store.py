"""MemoryStore: namespaced long-term memory over any KV backend (§27).

Sessions are per-conversation; a `MemoryStore` is the explicit cross-session
layer: small JSON records keyed by `(namespace, key)`, with tags, TTL and a
deterministic keyword search (or a custom searcher). Namespaces are mandatory
(or set once on the store) so a recall cannot silently read another user's or
workspace's memory.

    memory = MemoryStore(backend, namespace=f"user:{user_id}")
    await memory.put("preference", {"tone": "concise"}, text="prefers concise answers")
    hits = await memory.search("how does the user like answers?", k=3)
"""

from __future__ import annotations

import asyncio
import builtins
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field

from ..checkpoints import KVBackend
from .text import keyword_score

Searcher = Callable[["MemoryRecord", str], float]


class MemoryRecord(BaseModel):
    """One long-term memory entry (JSON-safe, persisted as a KV value)."""

    namespace: str
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    tags: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True)
class MemoryHit:
    record: MemoryRecord
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 4), **self.record.model_dump(mode="json")}


def _default_score(record: MemoryRecord, query: str) -> float:
    text = record.text or json.dumps(
        record.value, ensure_ascii=False, sort_keys=True, default=str
    )
    return keyword_score(text, query, use_stems=True, fold_plurals=True)


class MemoryStore:
    """Namespaced records over `KVBackend` with TTL and keyword search."""

    def __init__(
        self,
        backend: KVBackend,
        *,
        namespace: str | None = None,
        prefix: str = "memory",
        clock: Callable[[], datetime] | None = None,
        searcher: Searcher | None = None,
    ):
        if namespace is not None and not namespace.strip():
            raise ValueError("namespace must be non-empty when provided")
        self.backend = backend
        self.namespace = namespace
        self.prefix = prefix
        self._clock = clock or (lambda: datetime.now(UTC))
        self._searcher = searcher or _default_score
        self._lock = asyncio.Lock()

    def _ns(self, namespace: str | None) -> str:
        resolved = namespace or self.namespace
        if not resolved or not resolved.strip():
            raise ValueError(
                "MemoryStore needs a namespace on the store or per operation"
            )
        return resolved

    def _key(self, namespace: str, key: str) -> str:
        return f"{self.prefix}:{quote(namespace, safe='')}:{quote(key, safe='')}"

    def _namespace_prefix(self, namespace: str) -> str:
        return f"{self.prefix}:{quote(namespace, safe='')}:"

    def _expired(self, record: MemoryRecord, now: datetime) -> bool:
        return record.expires_at is not None and record.expires_at <= now

    async def _load(self, namespace: str, key: str) -> MemoryRecord | None:
        payload = await self.backend.get(self._key(namespace, key))
        if payload is None:
            return None
        return MemoryRecord.model_validate(payload)

    async def put(
        self,
        key: str,
        value: Mapping[str, Any] | None = None,
        *,
        namespace: str | None = None,
        text: str | None = None,
        tags: Mapping[str, str] | None = None,
        ttl_seconds: float | None = None,
    ) -> MemoryRecord:
        """Creates or replaces a record (`created_at` is preserved on replace)."""
        if not key.strip():
            raise ValueError("memory key must be non-empty")
        ns = self._ns(namespace)
        now = self._clock()
        async with self._lock:
            existing = await self._load(ns, key)
            record = MemoryRecord(
                namespace=ns,
                key=key,
                value=dict(value or {}),
                text=(
                    text if text is not None else (existing.text if existing else "")
                ),
                tags=dict(tags or (existing.tags if existing else {})),
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
                expires_at=(
                    now + timedelta(seconds=ttl_seconds)
                    if ttl_seconds is not None
                    else None
                ),
            )
            await self.backend.set(self._key(ns, key), record.model_dump(mode="json"))
        return record

    async def get(
        self, key: str, *, namespace: str | None = None
    ) -> MemoryRecord | None:
        ns = self._ns(namespace)
        record = await self._load(ns, key)
        if record is None:
            return None
        if self._expired(record, self._clock()):
            await self.backend.delete(self._key(ns, key))
            return None
        return record

    async def delete(self, key: str, *, namespace: str | None = None) -> bool:
        ns = self._ns(namespace)
        async with self._lock:
            existed = await self.backend.get(self._key(ns, key)) is not None
            await self.backend.delete(self._key(ns, key))
        return existed

    async def list(
        self,
        *,
        namespace: str | None = None,
        key_prefix: str = "",
    ) -> builtins.list[MemoryRecord]:
        """Unexpired records in one namespace, newest first."""
        ns = self._ns(namespace)
        prefix = self._namespace_prefix(ns)
        now = self._clock()
        records: builtins.list[MemoryRecord] = []
        for storage_key in await self.backend.keys():
            if not storage_key.startswith(prefix):
                continue
            record = await self._load(ns, _unquote_key(storage_key, prefix))
            if record is None or self._expired(record, now):
                continue
            if key_prefix and not record.key.startswith(key_prefix):
                continue
            records.append(record)
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records

    async def search(
        self,
        query: str,
        *,
        namespace: str | None = None,
        k: int = 5,
        min_score: float = 0.0,
        tags: Mapping[str, str] | None = None,
    ) -> builtins.list[MemoryHit]:
        """Keyword-ranked records in one namespace (ties: newest first)."""
        if k < 1:
            return []
        records = await self.list(namespace=namespace)
        if tags:
            records = [
                record
                for record in records
                if all(record.tags.get(key) == value for key, value in tags.items())
            ]
        hits = [
            MemoryHit(record=record, score=float(self._searcher(record, query)))
            for record in records
        ]
        if query.strip():
            # A non-empty query must have at least one matching term; unlike
            # `list()`, `search("...")` should not return every record at 0.0.
            hits = [hit for hit in hits if hit.score > 0]
        hits = [hit for hit in hits if hit.score >= min_score]
        hits.sort(
            key=lambda hit: (hit.score, hit.record.updated_at),
            reverse=True,
        )
        return hits[:k]

    async def forget(self, *, namespace: str | None = None) -> int:
        """Deletes every record in a namespace; returns the number removed."""
        ns = self._ns(namespace)
        prefix = self._namespace_prefix(ns)
        keys = [key for key in await self.backend.keys() if key.startswith(prefix)]
        async with self._lock:
            for key in keys:
                await self.backend.delete(key)
        return len(keys)


def _unquote_key(storage_key: str, prefix: str) -> str:
    from urllib.parse import unquote

    return unquote(storage_key[len(prefix) :])


__all__ = ["MemoryHit", "MemoryRecord", "MemoryStore", "Searcher"]
