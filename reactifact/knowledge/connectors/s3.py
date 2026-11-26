"""S3 connector (optional ``knowledge-s3`` extra)."""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..._extras import require_extra
from ..errors import SourceError
from ..models import DocumentMeta, RawDocument

_FORMAT_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
}


class S3Connector:
    """Fetches objects from one S3 bucket, using ETags for incremental listing."""

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        source_id: str = "s3",
        extensions: Sequence[str] | None = None,
        client: Any | None = None,
        region_name: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.source_id = source_id
        self.extensions = (
            tuple(extension.lower() for extension in extensions)
            if extensions is not None
            else None
        )
        if client is None:
            boto3 = require_extra("S3Connector", "boto3", "knowledge-s3")
            client = boto3.client(
                "s3",
                region_name=region_name,
                endpoint_url=endpoint_url,
            )
        self._client = client

    async def aclose(self) -> None:
        closer = getattr(self._client, "close", None)
        if callable(closer):
            await asyncio.to_thread(closer)

    async def list_documents(self) -> list[DocumentMeta]:
        return await asyncio.to_thread(self._list)

    async def fetch(self, *, uris: set[str] | None = None) -> list[RawDocument]:
        if uris is None:
            keys = [meta.uri for meta in await self.list_documents()]
        else:
            keys = sorted(uris)
        return [await asyncio.to_thread(self._get, key) for key in keys]

    def _list(self) -> list[DocumentMeta]:
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket, Prefix=self.prefix)
            documents: list[DocumentMeta] = []
            for page in pages:
                for item in page.get("Contents", []):
                    key = str(item["Key"])
                    if not self._wanted(key):
                        continue
                    documents.append(
                        DocumentMeta(
                            uri=key,
                            step_id=key,
                            fingerprint=str(item.get("ETag", "")).strip('"'),
                            content_type=_content_type(key),
                        )
                    )
            return documents
        except Exception as exc:  # noqa: BLE001 — provider errors are domain errors
            raise SourceError(f"S3 listing failed for {self.bucket!r}: {exc}") from exc

    def _get(self, key: str) -> RawDocument:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"].read()
        except Exception as exc:  # noqa: BLE001 — provider errors are domain errors
            raise SourceError(
                f"S3 get_object failed for {self.bucket!r}/{key!r}: {exc}"
            ) from exc
        content_type = str(response.get("ContentType") or _content_type(key))
        return RawDocument(
            step_id=key,
            uri=key,
            content_type=content_type,
            data=body,
            metadata={
                "sourceUrl": f"s3://{self.bucket}/{key}",
                "title": Path(key).stem,
            },
        )

    def _wanted(self, key: str) -> bool:
        if not self.extensions:
            return True
        return key.lower().endswith(self.extensions)


def _content_type(key: str) -> str:
    suffix = Path(key).suffix.lower()
    if suffix in _FORMAT_TYPES:
        return _FORMAT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(key)
    return guessed or "application/octet-stream"


__all__ = ["S3Connector"]
