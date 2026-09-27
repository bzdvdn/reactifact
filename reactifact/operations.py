"""reactifact.operations — the runtime's compiled operations (§12, §15, §24).

`Patch` (the container) and `Effects` (the authoring surface) both ultimately
reduce to these dataclasses. They are what the runtime applies, validates,
commits and replays — the *compiled* form of "what changed". Applications
rarely touch them directly (they write `self.effects.*` instead).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import migrate_payload, resolve, type_id_of


@dataclass
class Operation:
    """Base operation in a patch."""

    def to_dict(self) -> dict[str, Any]:
        return {}


@dataclass
class Create(Operation):
    data: Any  # Pydantic model for the new artifact

    data_type: str = ""
    # Filled in by the runtime when the patch is applied: the real id of the created artifact.
    # Required for replay and rebuilding state from commits.
    artifact_id: str | None = None
    # Stable id (§42): if an artifact with such an id already exists, create
    # idempotently returns the existing one and does not create a duplicate.
    # Used for re-resolving sources without multiplying evidence/links.
    id: str | None = None

    def __post_init__(self) -> None:
        if not self.data_type and hasattr(self.data, "__class__"):
            self.data_type = type_id_of(type(self.data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "create",
            "data": self.data.model_dump(mode="json"),
            "data_type": self.data_type,
            "artifact_id": self.artifact_id,
            "id": self.id,
        }


@dataclass
class Update(Operation):
    artifact_id: str
    new_data: Any  # Pydantic model with updated data
    data_type: str = ""

    def __post_init__(self) -> None:
        if not self.data_type and hasattr(self.new_data, "__class__"):
            self.data_type = type_id_of(type(self.new_data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "update",
            "artifact_id": self.artifact_id,
            "data": self.new_data.model_dump(mode="json"),
            "data_type": self.data_type,
        }


@dataclass
class Delete(Operation):
    artifact_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "delete",
            "artifact_id": self.artifact_id,
        }


@dataclass(frozen=True)
class Relation:
    """Edges of the artifact graph (§15): a directed link `source —relation→ target`.

    Not an artifact, but a first-class dimension of the Context state: created/removed
    by Link/Unlink operations, serialized into commits and snapshots.
    """

    source_id: str
    relation: str
    target_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relation": self.relation,
            "target_id": self.target_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Relation:
        return cls(
            source_id=d["source_id"],
            relation=d["relation"],
            target_id=d["target_id"],
        )


@dataclass
class Link(Operation):
    """Sets the link `artifact_id —relation→ target_id` (§12, §15)."""

    artifact_id: str
    relation: str
    target_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "link",
            "artifact_id": self.artifact_id,
            "relation": self.relation,
            "target_id": self.target_id,
        }


@dataclass
class Unlink(Operation):
    """Removes links `artifact_id —relation→ *`.

    `relation`/`target_id` are optional: None = any. The pattern is resolved at
    apply time, so replayability is preserved.
    """

    artifact_id: str
    relation: str | None = None
    target_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "unlink",
            "artifact_id": self.artifact_id,
            "relation": self.relation,
            "target_id": self.target_id,
        }


def operation_from_dict(d: dict[str, Any]) -> Operation:
    op_type = d["type"]
    if op_type == "create":
        data_type = d["data_type"]
        model_class = resolve(data_type)
        data = model_class.model_validate(migrate_payload(data_type, d["data"]))
        return Create(
            data=data,
            id=d.get("id"),
            artifact_id=d.get("artifact_id"),
            data_type=data_type,
        )
    elif op_type == "update":
        data_type = d["data_type"]
        model_class = resolve(data_type)
        new_data = model_class.model_validate(migrate_payload(data_type, d["data"]))
        return Update(
            artifact_id=d["artifact_id"], new_data=new_data, data_type=data_type
        )
    elif op_type == "delete":
        return Delete(artifact_id=d["artifact_id"])
    elif op_type == "link":
        return Link(
            artifact_id=d["artifact_id"],
            relation=d["relation"],
            target_id=d["target_id"],
        )
    elif op_type == "unlink":
        return Unlink(
            artifact_id=d["artifact_id"],
            relation=d.get("relation"),
            target_id=d.get("target_id"),
        )
    else:
        raise ValueError(f"Unknown operation type: {op_type}")


__all__ = [
    "Create",
    "Delete",
    "Link",
    "Operation",
    "Relation",
    "Unlink",
    "Update",
    "operation_from_dict",
]
