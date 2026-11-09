from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .patches import Operation, operation_from_dict


@dataclass
class Read:
    """A record of a consumed artifact: the runtime builds links from consumes.

    Records the fact that the artifact was read at a specific revision
    (git-like ancestor).
    """

    artifact_id: str
    version: int

    def to_dict(self) -> dict[str, Any]:
        return {"artifact_id": self.artifact_id, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Read:
        return cls(artifact_id=d["artifact_id"], version=d["version"])


@dataclass
class Write:
    """A record of an artifact produced/changed by a single commit."""

    artifact_id: str
    version: int
    op_type: str  # create | update | delete
    #: sha256 of the payload at this version (content addressing). None for
    #: deletes and for older payloads; excluded from equality so structural
    #: comparisons in tests keep working.
    content_hash: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "artifact_id": self.artifact_id,
            "version": self.version,
            "op_type": self.op_type,
        }
        if self.content_hash is not None:
            payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Write:
        return cls(
            artifact_id=d["artifact_id"],
            version=d["version"],
            op_type=d["op_type"],
            content_hash=d.get("content_hash"),
        )


@dataclass
class Commit:
    """A record of one applied patch.

    Commits form a git-like chain: each commit knows its parent, was executed
    against a specific Context version and carries a reads/writes trace.
    """

    author: str
    message: str
    operations: list[Operation]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    parent_id: str | None = None
    context_version: int | None = None
    reads: list[Read] = field(default_factory=list)
    writes: list[Write] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "author": self.author,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "operations": [op.to_dict() for op in self.operations],
            "parent_id": self.parent_id,
            "context_version": self.context_version,
            "reads": [r.to_dict() for r in self.reads],
            "writes": [w.to_dict() for w in self.writes],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Commit:
        return cls(
            author=d["author"],
            message=d["message"],
            operations=[operation_from_dict(op) for op in d["operations"]],
            id=d["id"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            parent_id=d.get("parent_id"),
            context_version=d.get("context_version"),
            reads=[Read.from_dict(r) for r in d.get("reads", [])],
            writes=[Write.from_dict(w) for w in d.get("writes", [])],
        )
