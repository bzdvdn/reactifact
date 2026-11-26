"""ChromaDB index — an optional dense backend.

Chroma is a natural fit for small/medium local corpora, but it is a heavy
optional dependency, so `chromadb` is imported only when a real client is
created. Pass an existing client for tests or remote Chroma deployments:

    from reactifact.knowledge.indexes.chroma import ChromaIndex

    index = ChromaIndex(path=".reactifact/chroma", collection="knowledge")
    # or: ChromaIndex(client=my_chroma_client, collection="knowledge")

The collection is created with cosine distance. `KeywordIndexer` is not
implemented, so `hybrid=True` raises `ConfigError`; use `hybrid=False`
with this backend. Chroma also enforces the collection dimensionality
itself; this adapter additionally fails fast when the size is known.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..._extras import require_extra
from ..models import Chunk, SearchHit
from .acl import acl_visible
from .base import check_dimensions, chunk_point_id
from .math import clamp

_UPSERT_BATCH_SIZE = 500
_CANDIDATE_FACTOR = 4

_PAYLOAD_URI = "__uri"
_PAYLOAD_SOURCE = "__source"
_PAYLOAD_HASH = "__hash"
_PAYLOAD_DOCUMENT_ID = "__document_id"
_PAYLOAD_ACL = "__acl"
_PAYLOAD_METADATA = "__metadata"
_PAYLOAD_SCHEMA = "__schema"
_PAYLOAD_SCHEMA_VERSION = 1
_SYSTEM_PREFIX = "__"


def _is_system_key(key: str) -> bool:
    return key.startswith(_SYSTEM_PREFIX)


def _document_identity(chunk: Chunk) -> str:
    declared = chunk.metadata.get("documentId")
    if declared is None:
        declared = chunk.metadata.get("document_id")
    return str(declared) if declared else chunk.source_uri


class ChromaIndex:
    """ChromaDB-backed dense index (requires ``reactifact[knowledge-chroma]``).

    IDs are deterministic (``point_id``), so re-upserting identical content
    replaces instead of duplicating. Reconcile is per source and uses the
    reserved ``__source``/``__uri``/``__hash`` metadata fields.
    """

    def __init__(
        self,
        path: str = ".reactifact/chroma",
        collection: str = "knowledge",
        *,
        client: Any | None = None,
    ) -> None:
        self.path = path
        self.collection = collection
        self._dimensions: int | None = None
        if client is None:
            chromadb = require_extra(
                "knowledge.indexes.ChromaIndex",
                "chromadb",
                "knowledge-chroma",
            )
            self._client = chromadb.PersistentClient(path=path)
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False
        self._collection = self._client.get_or_create_collection(
            name=collection,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def signature(self) -> str:
        return f"chroma:{self.collection}:{self.path}"

    async def upsert(
        self,
        chunk: Chunk,
        vector: list[float],
        *,
        source_id: str,
        artifact_hash: str,
    ) -> None:
        await self.upsert_batch([(chunk, vector, artifact_hash)], source_id=source_id)

    async def upsert_batch(
        self,
        items: Sequence[tuple[Chunk, list[float], str]],
        *,
        source_id: str,
    ) -> None:
        if not items:
            return
        for _, vector, _ in items:
            self._dimensions = check_dimensions(
                self._dimensions, vector, backend="chroma"
            )
        ids: list[str] = []
        embeddings: list[list[float]] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for chunk, vector, artifact_hash in items:
            self._validate_metadata(chunk.metadata)
            ids.append(str(chunk_point_id(chunk)))
            embeddings.append(vector)
            documents.append(chunk.text)
            metadatas.append(self._metadata(chunk, source_id, artifact_hash))
        for start in range(0, len(ids), _UPSERT_BATCH_SIZE):
            stop = start + _UPSERT_BATCH_SIZE
            self._collection.upsert(
                ids=ids[start:stop],
                embeddings=embeddings[start:stop],
                documents=documents[start:stop],
                metadatas=metadatas[start:stop],
            )

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        page = self._collection.get(
            where={_PAYLOAD_SOURCE: source_id},
            include=["metadatas"],
        )
        ids = page.get("ids") or []
        metadatas = page.get("metadatas") or []
        stale: list[str] = []
        for pid, metadata in zip(ids, metadatas, strict=False):
            if not isinstance(metadata, dict):
                continue
            uri = metadata.get(_PAYLOAD_URI)
            hash_value = metadata.get(_PAYLOAD_HASH)
            if not isinstance(uri, str):
                stale.append(str(pid))
                continue
            if uri not in current or current[uri] != hash_value:
                stale.append(str(pid))
        if stale:
            self._collection.delete(ids=stale)

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
        source_id: str | None = None,
    ) -> list[SearchHit]:
        available = int(self._collection.count())
        if available == 0:
            return []
        if self._dimensions is not None:
            check_dimensions(self._dimensions, vector, backend="chroma")
        over_fetch = top_k * _CANDIDATE_FACTOR if principals is not None else top_k
        n_results = min(max(1, over_fetch), available)
        kwargs: dict[str, Any] = {
            "query_embeddings": [vector],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        clauses: list[dict[str, Any]] = [
            {str(key): value} for key, value in (filters or {}).items()
        ]
        if source_id is not None:
            clauses.append({_PAYLOAD_SOURCE: source_id})
        if len(clauses) == 1:
            kwargs["where"] = clauses[0]
        elif clauses:
            kwargs["where"] = {"$and": clauses}
        result = self._collection.query(**kwargs)
        ids = _first(result.get("ids"))
        documents = _first(result.get("documents"))
        metadatas = _first(result.get("metadatas"))
        distances = _first(result.get("distances"))

        hits: list[SearchHit] = []
        for _pid, document, metadata, distance in zip(
            ids, documents, metadatas, distances, strict=False
        ):
            if metadata is None or document is None:
                continue
            payload = _metadata_of(metadata)
            if not acl_visible(payload, principals):
                continue
            hits.append(
                SearchHit(
                    text=str(document),
                    source_uri=str(metadata.get(_PAYLOAD_URI, "")),
                    metadata=payload,
                    score=clamp(1.0 - float(distance)),
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    async def count(self) -> int:
        return int(self._collection.count())

    async def aclose(self) -> None:
        if not self._owns_client:
            return
        closer = getattr(self._client, "close", None)
        if callable(closer):
            closer()

    @staticmethod
    def _validate_metadata(metadata: Mapping[str, object]) -> None:
        reserved = sorted(key for key in metadata if key.startswith(_SYSTEM_PREFIX))
        if reserved:
            raise ValueError(
                f"metadata keys starting with {_SYSTEM_PREFIX!r} are reserved "
                f"for the engine: {reserved}"
            )

    @staticmethod
    def _metadata(
        chunk: Chunk,
        source_id: str,
        artifact_hash: str,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            _PAYLOAD_URI: chunk.source_uri,
            _PAYLOAD_SOURCE: source_id,
            _PAYLOAD_HASH: artifact_hash,
            _PAYLOAD_DOCUMENT_ID: _document_identity(chunk),
            _PAYLOAD_SCHEMA: _PAYLOAD_SCHEMA_VERSION,
            _PAYLOAD_METADATA: json.dumps(
                chunk.metadata,
                ensure_ascii=False,
                default=str,
            ),
        }
        for key, value in chunk.metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                metadata[key] = str(value)
        acl = chunk.metadata.get("acl")
        if isinstance(acl, (list, tuple)) and acl:
            metadata[_PAYLOAD_ACL] = json.dumps([str(principal) for principal in acl])
        return metadata


def _first(value: Any) -> list[Any]:
    if not value:
        return []
    first = value[0]
    return list(first) if isinstance(first, (list, tuple)) else list(value)


def _metadata_of(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = metadata.get(_PAYLOAD_METADATA)
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["ChromaIndex"]
