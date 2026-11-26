"""Typed values that flow through the knowledge pipeline.

The pipeline is intentionally linear and separate from the reactive agent
runtime::

    Connector -> RawDocument -> Document -> Chunk -> vector -> Index

`RawDocument` is the fetched body. It is *not* `reactifact.Artifact` — the
name collision would make both APIs unreadable, so the knowledge layer keeps
its own vocabulary and only bridges to `reactifact.Source` explicitly
(see `reactifact.knowledge.bridge`).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CCT_SCHEMA_VERSION = 1
"""Current Canonical Content Tree schema version (bumped only by RFC)."""


def point_id(
    source_uri: str,
    chunk_text: str,
    *,
    chunk_index: int | str | None = None,
) -> uuid.UUID:
    """Deterministic point ID so identical content replaces, not duplicates.

    `chunk_index` (when the chunker records it) disambiguates two identical
    bodies in one document — without it they would collapse into one point.
    """
    identity = source_uri if chunk_index is None else f"{source_uri}#{chunk_index}"
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{identity}\n{chunk_text}")


class RawDocument(BaseModel):
    """A raw snapshot of one document obtained from a connector."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    uri: str
    content_type: str = "text/markdown"
    data: bytes
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_text(
        cls,
        *,
        step_id: str,
        uri: str,
        text: str,
        title: str = "",
        metadata: dict[str, Any] | None = None,
        content_type: str = "text/markdown",
    ) -> RawDocument:
        """Build a document from text — the common code-first case."""
        values = dict(metadata or {})
        if title:
            values.setdefault("title", title)
        return cls(
            step_id=step_id,
            uri=uri,
            content_type=content_type,
            data=text.encode("utf-8"),
            metadata=values,
        )


class DocumentMeta(BaseModel):
    """Cheap listing metadata, available before the body is fetched.

    `fingerprint` identifies the document's current state from listing data
    alone (S3 ETag, GitLab/GitHub blob sha, Confluence version, local file
    hash). Incremental ingestion compares it against the docstore and skips
    downloading unchanged documents.
    """

    model_config = ConfigDict(frozen=True)

    uri: str
    step_id: str
    fingerprint: str
    content_type: str | None = None


class Section(BaseModel):
    """A heading-anchored block of the Canonical Content Tree."""

    model_config = ConfigDict(frozen=True)

    heading: str
    body: str


class Document(BaseModel):
    """Canonical Content Tree v1 — the intermediate form before chunking."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = CCT_SCHEMA_VERSION
    uri: str
    title: str
    sections: tuple[Section, ...] = Field(default_factory=tuple)


class Chunk(BaseModel):
    """A piece of the canonical content ready for embedding."""

    model_config = ConfigDict(frozen=True)

    text: str
    source_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def chunk_point_id(chunk: Chunk) -> uuid.UUID:
    """Deterministic point ID for one chunk (see `point_id`)."""
    index = chunk.metadata.get("chunk_index")
    return point_id(
        chunk.source_uri,
        chunk.text,
        chunk_index=index if isinstance(index, (int, str)) else None,
    )


class SearchHit(BaseModel):
    """A stored chunk matched against a query vector."""

    model_config = ConfigDict(frozen=True)

    text: str
    source_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float
    dense_rank: int | None = None
    keyword_rank: int | None = None
    rerank_rank: int | None = None
    score_kind: Literal["dense", "rrf"] = "dense"

    @property
    def chunk_id(self) -> str:
        """Stable chunk identity (same value the index uses as its point id)."""
        index = self.metadata.get("chunk_index")
        return str(
            point_id(
                self.source_uri,
                self.text,
                chunk_index=index if isinstance(index, (int, str)) else None,
            )
        )


def content_hash(document: RawDocument) -> str:
    """Deterministic document hash used for dedup and reconciliation.

    Includes the content type and metadata, not just the bytes: a title/ACL/
    tag change must re-index the document even when its text is unchanged.
    """
    digest = hashlib.sha256(document.data)
    digest.update(document.content_type.encode("utf-8"))
    digest.update(
        json.dumps(
            document.metadata,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    )
    return digest.hexdigest()


__all__ = [
    "CCT_SCHEMA_VERSION",
    "Chunk",
    "Document",
    "DocumentMeta",
    "RawDocument",
    "SearchHit",
    "chunk_point_id",
    "Section",
    "content_hash",
    "point_id",
]
