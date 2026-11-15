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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CCT_SCHEMA_VERSION = 1
"""Current Canonical Content Tree schema version (bumped only by RFC)."""


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


class SearchHit(BaseModel):
    """A stored chunk matched against a query vector."""

    model_config = ConfigDict(frozen=True)

    text: str
    source_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float


def content_hash(document: RawDocument) -> str:
    """Deterministic content hash used for dedup and reconciliation."""
    return hashlib.sha256(document.data).hexdigest()


__all__ = [
    "CCT_SCHEMA_VERSION",
    "Chunk",
    "Document",
    "DocumentMeta",
    "RawDocument",
    "SearchHit",
    "Section",
    "content_hash",
]
