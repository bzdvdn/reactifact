"""MemoryStore: namespaced long-term memory over any KV backend (§27).

Sessions are per-conversation; a `MemoryStore` is the explicit cross-session
layer: small JSON records keyed by `(namespace, key)`, with tags, TTL and a
deterministic keyword search (or a custom searcher). Namespaces are mandatory
(or set once on the store) so a recall cannot silently read another user's or
workspace's memory.

The high-level surface is two verbs — `remember` and `recall` — plus the
artifact-aware `remember_artifact`:

    memory = MemoryStore(backend, namespace=f"user:{user_id}")
    await memory.remember(
        "prefers concise answers with citations",
        tags={"kind": "preference"},
    )
    hits = await memory.recall("how does the user like answers?", k=3)

`remember` derives a stable key from the text when one is omitted, so the
caller does not hand-manage a record id. The lower-level `put`/`get`/`list`/
`search` methods remain public for migrations and custom indexes.

For a write that must happen only after the artifact commits, a `@step`
returns `remember_later(artifact, ...)` — an `Ensure(PendingAction)` the
runtime writes like any other return value; the app passes
`dispatcher=MemoryStore.dispatch` so the recipe performs the actual write
after the generation commits:

    @step(produces=PendingAction, reads=Report)
    async def remember(report: Artifact[Report]) -> Ensure:
        return memory.remember_later(report, key=f"report:{report.id}", ...)

`remember_after_commit(effects, ...)` is the 0.x effects-slot spelling kept for
compatibility.
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from pydantic import BaseModel, Field

from ..agent import Ensure
from ..checkpoints import KVBackend
from ..interaction import Outbox
from ..interrupt import PendingAction
from .text import keyword_score

if TYPE_CHECKING:
    from ..artifacts import Artifact
    from ..context import Context
    from ..effects import Effects, Handle

#: Outbox action kind for `remember_after_commit` / `MemoryStore.dispatch`.
MEMORY_ACTION = "memory.remember"

Searcher = Callable[["MemoryRecord", str], float]


def _stable_key(text: str) -> str:
    """A deterministic, readable key for a memory remembered without one."""
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]
    return f"text:{digest}"


def _artifact_text(artifact: Artifact[Any]) -> str:
    """The searchable default text of an artifact (its `text`, else JSON)."""
    data = artifact.data
    text = getattr(data, "text", None)
    if isinstance(text, str):
        return text
    return json.dumps(
        (
            data.model_dump(mode="json")
            if isinstance(data, BaseModel)
            else {"value": str(data)}
        ),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


class MemoryRecord(BaseModel):
    """One long-term memory entry (JSON-safe, persisted as a KV value).

    `source_*` fields point back to the Context artifact a curated memory came
    from; the session/checkpoint remains the source of truth, this is a
    rebuildable cross-session index entry.
    """

    namespace: str
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    tags: dict[str, str] = Field(default_factory=dict)
    kind: str = "fact"
    source_session: str = ""
    source_artifact: str = ""
    source_version: int | None = None
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
    """Namespaced long-term memory with TTL and keyword recall.

    The high-level API is `remember`/`recall` plus `remember_artifact` and
    `remember_later`; `put`/`get`/`list`/`search` are the lower-level engine
    methods kept for custom indexes and migrations.
    """

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
        kind: str | None = None,
        source_session: str | None = None,
        source_artifact: str | None = None,
        source_version: int | None = None,
    ) -> MemoryRecord:
        """Low-level write: creates or replaces a record by explicit key.

        `remember` is the high-level spelling; `created_at` is preserved when a
        key is replaced.
        """
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
                kind=(
                    kind
                    if kind is not None
                    else (existing.kind if existing is not None else "fact")
                ),
                source_session=(
                    source_session
                    if source_session is not None
                    else (existing.source_session if existing is not None else "")
                ),
                source_artifact=(
                    source_artifact
                    if source_artifact is not None
                    else (existing.source_artifact if existing is not None else "")
                ),
                source_version=(
                    source_version
                    if source_version is not None
                    else (existing.source_version if existing is not None else None)
                ),
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

    async def remember(
        self,
        text: str,
        *,
        key: str | None = None,
        value: Mapping[str, Any] | BaseModel | None = None,
        tags: Mapping[str, str] | None = None,
        ttl_seconds: float | None = None,
        kind: str = "fact",
        source_session: str = "",
        source_artifact: str = "",
        source_version: int | None = None,
        namespace: str | None = None,
    ) -> MemoryRecord:
        """High-level write: searchable text plus optional structured value.

        The key is derived from the normalized text when omitted, so the common
        "remember this fact" call has no storage key to invent. Pass one
        explicitly when the same fact must replace an earlier entry under a
        stable domain key. `value` may be a mapping or any pydantic model.
        """
        if not text.strip() and value is None:
            raise ValueError("remember needs non-empty text or value")
        storage_value = (
            value.model_dump(mode="json")
            if isinstance(value, BaseModel)
            else dict(value or {})
        )
        searchable = text.strip() or json.dumps(
            storage_value, ensure_ascii=False, sort_keys=True, default=str
        )
        resolved_key = key or _stable_key(searchable)
        return await self.put(
            resolved_key,
            storage_value,
            namespace=namespace,
            text=searchable,
            tags=tags,
            ttl_seconds=ttl_seconds,
            kind=kind,
            source_session=source_session,
            source_artifact=source_artifact,
            source_version=source_version,
        )

    async def get(
        self, key: str, *, namespace: str | None = None
    ) -> MemoryRecord | None:
        """Low-level read by explicit key (expired records are removed)."""
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
        """Low-level search: keyword-ranked records (ties: newest first).

        `recall` is the high-level spelling with the same semantics.
        """
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

    async def recall(
        self,
        query: str,
        *,
        namespace: str | None = None,
        k: int = 5,
        min_score: float = 0.0,
        tags: Mapping[str, str] | None = None,
    ) -> builtins.list[MemoryHit]:
        """High-level search spelling; same ranking and filters as `search`."""
        return await self.search(
            query,
            namespace=namespace,
            k=k,
            min_score=min_score,
            tags=tags,
        )

    async def remember_artifact(
        self,
        artifact: Artifact[Any],
        *,
        key: str | None = None,
        source_session: str,
        text: str | None = None,
        value: Mapping[str, Any] | None = None,
        tags: Mapping[str, str] | None = None,
        ttl_seconds: float | None = None,
        namespace: str | None = None,
        kind: str = "summary",
    ) -> MemoryRecord:
        """Indexes a curated artifact, recording its session/id/version as source.

        The artifact stays the source of truth; by default this stores only a
        pointer (`value={"artifact": id, "data_type": …}`) plus the searchable
        `text` you choose (or the artifact's `text`/JSON when omitted).

        `source_session` is explicit, not ambient: pass the session the entry
        came from (often `request`/`UserMsg.session_id`, or `""` when the
        memory is not tied to one) so `forget_source()` can erase everything
        derived from that session later. `key` defaults to
        `<data-type>:<artifact-id>`, which is stable across re-indexing.
        """
        if text is None:
            text = _artifact_text(artifact)
        if value is None:
            value = {"artifact": artifact.id, "data_type": artifact.data_type}
        resolved_key = key or f"{type(artifact.data).__name__.lower()}:{artifact.id}"
        return await self.put(
            resolved_key,
            value,
            namespace=namespace,
            text=text,
            tags=tags,
            ttl_seconds=ttl_seconds,
            kind=kind,
            source_session=source_session,
            source_artifact=artifact.id,
            source_version=artifact.version,
        )

    #: Compatibility alias for `remember_artifact`.
    remember_from = remember_artifact

    def _action_payload(
        self,
        artifact: Artifact[Any],
        *,
        key: str,
        source_session: str,
        text: str | None,
        tags: Mapping[str, str] | None,
        ttl_seconds: float | None,
        kind: str,
    ) -> dict[str, Any]:
        if not key.strip():
            raise ValueError("memory key must be non-empty")
        return {
            "key": key,
            "text": text if text is not None else _artifact_text(artifact),
            "kind": kind,
            "tags": dict(tags or {}),
            "ttl_seconds": ttl_seconds,
            "source_session": source_session,
            "source_artifact": artifact.id,
            "source_version": artifact.version,
            "data_type": artifact.data_type,
        }

    def remember_later(
        self,
        artifact: Artifact[Any],
        *,
        key: str,
        source_session: str,
        text: str | None = None,
        tags: Mapping[str, str] | None = None,
        ttl_seconds: float | None = None,
        kind: str = "summary",
    ) -> Ensure:
        """Return an `Ensure(PendingAction)` for a post-commit memory write.

        Use it as a `@step` return value instead of wiring `Effects`/`Outbox`
        at the call site:

            @step(produces=PendingAction, reads=Report)
            async def remember(report: Artifact[Report]) -> Ensure:
                return memory.remember_later(
                    report, key=f"report:{report.id}", source_session=session_id
                )

        The runtime writes the action like any other return instruction;
        `MemoryStore.dispatch` performs the actual write after the generation
        commits, so a failed turn never leaves a dangling memory entry.
        """
        payload = self._action_payload(
            artifact,
            key=key,
            source_session=source_session,
            text=text,
            tags=tags,
            ttl_seconds=ttl_seconds,
            kind=kind,
        )
        action = PendingAction(
            kind=MEMORY_ACTION,
            idempotency_key=f"memory:{key}",
            payload=payload,
        )
        return Ensure(action, id=f"action:memory:{key}")

    def remember_after_commit(
        self,
        effects: Effects,
        artifact: Artifact[Any],
        *,
        key: str,
        source_session: str,
        text: str | None = None,
        tags: Mapping[str, str] | None = None,
        ttl_seconds: float | None = None,
        kind: str = "summary",
    ) -> Handle | None:
        """Compatibility wrapper for `remember_later` in an effects slot.

        New step code returns `remember_later(...)`; this spelling stays for
        callers that already pass `effects` explicitly.
        """
        payload = self._action_payload(
            artifact,
            key=key,
            source_session=source_session,
            text=text,
            tags=tags,
            ttl_seconds=ttl_seconds,
            kind=kind,
        )
        return Outbox(effects).act(MEMORY_ACTION, key=f"memory:{key}", payload=payload)

    @classmethod
    async def dispatch(cls, context: Context, action: Artifact[PendingAction]) -> None:
        """Outbox dispatcher for `MEMORY_ACTION`; ignores other action kinds.

        The store is resolved from `context.resources`, so a per-turn
        `MemoryStore` (the usual per-user namespace) is the one written to.
        """
        if action.data.kind != MEMORY_ACTION:
            return
        store = context.resources.get(cls)
        if store is None:
            raise LookupError(
                "MemoryStore.dispatch: register the store on RuntimeResources"
            )
        payload: dict[str, Any] = action.data.payload
        key = payload.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("memory action is missing its key")
        source_artifact = str(payload.get("source_artifact", ""))
        source_version = payload.get("source_version")
        ttl = payload.get("ttl_seconds")
        tags = payload.get("tags")
        text = payload.get("text")
        await store.put(
            key,
            {
                "artifact": source_artifact,
                "data_type": str(payload.get("data_type", "")),
            },
            text=text if isinstance(text, str) else "",
            tags=(
                {str(name): str(value) for name, value in tags.items()}
                if isinstance(tags, dict)
                else None
            ),
            ttl_seconds=float(ttl) if isinstance(ttl, (int, float)) else None,
            kind=str(payload.get("kind", "summary")),
            source_session=str(payload.get("source_session", "")),
            source_artifact=source_artifact,
            source_version=source_version if isinstance(source_version, int) else None,
        )

    async def forget_source(
        self, source_session: str, *, namespace: str | None = None
    ) -> int:
        """Deletes records derived from one session; returns the number removed.

        An empty `source_session` is rejected: records without a session are
        not a single erasure unit, so deleting them by the empty key would be a
        surprising mass delete.
        """
        if not source_session.strip():
            raise ValueError("forget_source needs a non-empty source_session")
        ns = self._ns(namespace)
        records = await self.list(namespace=ns)
        keys = [
            record.key for record in records if record.source_session == source_session
        ]
        async with self._lock:
            for key in keys:
                await self.backend.delete(self._key(ns, key))
        return len(keys)

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


__all__ = [
    "MEMORY_ACTION",
    "MemoryHit",
    "MemoryRecord",
    "MemoryStore",
    "Searcher",
]
