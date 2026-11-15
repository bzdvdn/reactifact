"""Qdrant index — minimal client speaking the Qdrant REST API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from ..errors import SourceError
from ..http import HttpClient
from ..models import Chunk, SearchHit
from .base import point_id
from .keyword import bm25_scores, tokenize

_SCROLL_LIMIT = 100
_UPSERT_BATCH_SIZE = 100
_KEYWORD_CANDIDATE_FACTOR = 20
_KEYWORD_CANDIDATE_CAP = 500

_PAYLOAD_TEXT = "__text"
_PAYLOAD_URI = "__uri"
_PAYLOAD_SOURCE = "__source"
_PAYLOAD_HASH = "__hash"
_PAYLOAD_DOCUMENT_ID = "__document_id"
_PAYLOAD_ACL = "__acl"
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


class QdrantIndex:
    """Creates the collection on demand and upserts vector points."""

    def __init__(
        self,
        url: str,
        collection: str,
        *,
        api_token: str | None = None,
        api_token_env: str | None = None,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.collection = collection
        self.client = HttpClient(
            base_url=url,
            api_token=api_token,
            api_token_env=api_token_env,
            retries=retries,
            retry_backoff=retry_backoff,
            verify=verify,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

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
        for chunk, _, _ in items:
            self._validate_metadata(chunk.metadata)
        await self._ensure_collection(len(items[0][1]))
        points = [
            self._point(chunk, vector, source_id, artifact_hash)
            for chunk, vector, artifact_hash in items
        ]
        for start in range(0, len(points), _UPSERT_BATCH_SIZE):
            batch = points[start : start + _UPSERT_BATCH_SIZE]
            await self.client.request(
                "PUT",
                f"/collections/{self.collection}/points",
                query={"wait": "true"},
                payload={"points": batch},
            )

    @staticmethod
    def _point(
        chunk: Chunk,
        vector: list[float],
        source_id: str,
        artifact_hash: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            _PAYLOAD_TEXT: chunk.text,
            _PAYLOAD_URI: chunk.source_uri,
            _PAYLOAD_SOURCE: source_id,
            _PAYLOAD_HASH: artifact_hash,
            _PAYLOAD_DOCUMENT_ID: _document_identity(chunk),
            _PAYLOAD_SCHEMA: _PAYLOAD_SCHEMA_VERSION,
            **chunk.metadata,
        }
        acl = chunk.metadata.get("acl")
        if acl:
            payload[_PAYLOAD_ACL] = [str(principal) for principal in acl]
        return {
            "id": str(point_id(chunk.source_uri, chunk.text)),
            "vector": vector,
            "payload": payload,
        }

    @staticmethod
    def _validate_metadata(metadata: Mapping[str, object]) -> None:
        reserved = sorted(key for key in metadata if _is_system_key(key))
        if reserved:
            raise ValueError(
                f"metadata keys starting with {_SYSTEM_PREFIX!r} are reserved "
                f"for the engine: {reserved}"
            )

    async def reconcile(
        self,
        source_id: str,
        current: Mapping[str, str],
    ) -> None:
        filter_payload = {
            "must": [{"key": _PAYLOAD_SOURCE, "match": {"value": source_id}}]
        }
        stale_ids: list[str] = []
        offset: str | None = None
        while True:
            scroll: dict[str, object] = {
                "filter": filter_payload,
                "limit": _SCROLL_LIMIT,
                "with_payload": True,
            }
            if offset:
                scroll["offset"] = offset
            page = await self.client.request(
                "POST",
                f"/collections/{self.collection}/points/scroll",
                payload=scroll,
            )
            result = page.get("result", {})
            for point in result.get("points", []):
                payload = point.get("payload", {})
                uri = payload.get(_PAYLOAD_URI)
                hash_value = payload.get(_PAYLOAD_HASH)
                if uri not in current or current[uri] != hash_value:
                    stale_ids.append(point["id"])
            offset = result.get("next_page_offset")
            if not offset or not result.get("points"):
                break

        if stale_ids:
            await self.client.request(
                "POST",
                f"/collections/{self.collection}/points/delete",
                payload={"points": stale_ids},
            )

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        payload: dict[str, object] = {
            "vector": vector,
            "limit": top_k,
            "with_payload": True,
        }
        must: list[dict[str, object]] = [
            {"key": key, "match": {"value": value}}
            for key, value in (filters or {}).items()
        ]
        if principals is not None:
            must.append(
                {
                    "should": [
                        {"is_empty": {"key": _PAYLOAD_ACL}},
                        {
                            "key": _PAYLOAD_ACL,
                            "match": {"any": list(principals)},
                        },
                    ]
                }
            )
        if must:
            payload["filter"] = {"must": must}
        response = await self.client.request(
            "POST",
            f"/collections/{self.collection}/points/search",
            payload=payload,
        )
        return [
            SearchHit(
                text=str(item.get("payload", {}).get(_PAYLOAD_TEXT, "")),
                source_uri=str(item.get("payload", {}).get(_PAYLOAD_URI, "")),
                metadata={
                    key: value
                    for key, value in item.get("payload", {}).items()
                    if not _is_system_key(key)
                },
                score=float(item.get("score", 0.0)),
            )
            for item in response.get("result", [])
        ]

    async def keyword_search(
        self,
        text: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, str] | None = None,
        principals: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        query_tokens = tokenize(text)
        if not query_tokens:
            return []
        await self._ensure_text_index()
        must: list[dict[str, object]] = [
            {"key": key, "match": {"value": value}}
            for key, value in (filters or {}).items()
        ]
        if principals is not None:
            must.append(
                {
                    "should": [
                        {"is_empty": {"key": _PAYLOAD_ACL}},
                        {
                            "key": _PAYLOAD_ACL,
                            "match": {"any": list(principals)},
                        },
                    ]
                }
            )
        filter_payload: dict[str, object] = {
            "should": [
                {"key": _PAYLOAD_TEXT, "match": {"text": token}}
                for token in dict.fromkeys(query_tokens)
            ]
        }
        if must:
            filter_payload["must"] = must
        limit = min(
            max(top_k * _KEYWORD_CANDIDATE_FACTOR, 50),
            _KEYWORD_CANDIDATE_CAP,
        )
        try:
            response = await self.client.request(
                "POST",
                f"/collections/{self.collection}/points/scroll",
                payload={
                    "filter": filter_payload,
                    "limit": limit,
                    "with_payload": True,
                },
            )
        except SourceError as exc:
            if exc.status_code == 404:
                return []
            raise
        points = response.get("result", {}).get("points", [])
        if not points:
            return []
        documents = [
            tokenize(str(point.get("payload", {}).get(_PAYLOAD_TEXT, "")))
            for point in points
        ]
        scores = bm25_scores(query_tokens, documents)
        ranked = sorted(
            zip(points, scores, strict=True),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [
            SearchHit(
                text=str(point.get("payload", {}).get(_PAYLOAD_TEXT, "")),
                source_uri=str(point.get("payload", {}).get(_PAYLOAD_URI, "")),
                metadata={
                    key: value
                    for key, value in point.get("payload", {}).items()
                    if not _is_system_key(key)
                },
                score=float(score),
            )
            for point, score in ranked[:top_k]
        ]

    async def _ensure_text_index(self) -> None:
        try:
            await self.client.request(
                "PUT",
                f"/collections/{self.collection}/index",
                payload={
                    "field_name": _PAYLOAD_TEXT,
                    "field_schema": {
                        "type": "text",
                        "tokenizer": "word",
                        "lowercase": True,
                    },
                },
            )
        except SourceError as exc:
            if exc.status_code == 404:
                return
            raise

    async def count(self) -> int:
        response = await self.client.request(
            "GET",
            f"/collections/{self.collection}",
        )
        result: dict[str, Any] = response.get("result", {})
        count = result.get("points_count")
        return int(count) if isinstance(count, int) else 0

    async def _ensure_collection(self, dimensions: int) -> None:
        if await self._collection_exists():
            return
        await self.client.request(
            "PUT",
            f"/collections/{self.collection}",
            payload={"vectors": {"size": dimensions, "distance": "Cosine"}},
        )

    async def _collection_exists(self) -> bool:
        try:
            await self.client.request("GET", f"/collections/{self.collection}")
            return True
        except SourceError as exc:
            if exc.status_code == 404:
                return False
            raise


__all__ = ["QdrantIndex"]
