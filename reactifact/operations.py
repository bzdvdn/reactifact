"""reactifact.operations — the runtime's compiled operations (§12, §15, §24).

`Patch` (the container) and `Effects` (the authoring surface) both ultimately
reduce to these dataclasses. They are what the runtime applies, validates,
commits and replays — the *compiled* form of "what changed". Applications
rarely touch them directly (they write `effects.*` instead).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, ClassVar

from .artifacts import blob_key, payload_content_hash
from .types import migrate_payload, resolve, type_id_of


@dataclass
class Operation:
    """Base operation in a patch.

    Subclasses set `_type` (the serialized discriminant) and, when they carry a
    payload, `_payload_attr` (the dataclass field to serialize as `data`).
    """

    _type: ClassVar[str] = ""
    _payload_attr: ClassVar[str | None] = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self._type}
        for field_info in fields(self):
            name = field_info.name
            value = getattr(self, name)
            if name == self._payload_attr:
                payload["data"] = value.model_dump(mode="json")
            else:
                payload[name] = value
        return payload

    def to_ref_dict(self, blobs: dict[str, dict[str, Any]]) -> dict[str, Any]:
        payload = self.to_dict()
        if self._payload_attr is None:
            return payload
        data = payload.pop("data")
        data_type = payload.pop("data_type")
        content_hash = payload_content_hash(data)
        blobs[blob_key(data_type, content_hash)] = data
        payload["data_ref"] = {"type": data_type, "hash": content_hash}
        return payload


@dataclass
class Create(Operation):
    _type: ClassVar[str] = "create"
    _payload_attr: ClassVar[str | None] = "data"

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


@dataclass
class Update(Operation):
    _type: ClassVar[str] = "update"
    _payload_attr: ClassVar[str | None] = "new_data"

    artifact_id: str
    new_data: Any  # Pydantic model with updated data
    data_type: str = ""

    def __post_init__(self) -> None:
        if not self.data_type and hasattr(self.new_data, "__class__"):
            self.data_type = type_id_of(type(self.new_data))


@dataclass
class Delete(Operation):
    _type: ClassVar[str] = "delete"

    artifact_id: str


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

    _type: ClassVar[str] = "link"

    artifact_id: str
    relation: str
    target_id: str


@dataclass
class Unlink(Operation):
    """Removes links `artifact_id —relation→ *`.

    `relation`/`target_id` are optional: None = any. The pattern is resolved at
    apply time, so replayability is preserved.
    """

    _type: ClassVar[str] = "unlink"

    artifact_id: str
    relation: str | None = None
    target_id: str | None = None


def _payload_from_dict(
    d: dict[str, Any], blobs: dict[str, dict[str, Any]] | None
) -> tuple[str, Any]:
    ref = d.get("data_ref")
    if ref is not None:
        if blobs is None:
            raise ValueError("a data_ref operation needs the blob map to load")
        data_type = ref["type"]
        content_hash = ref["hash"]
        payload = blobs[blob_key(data_type, content_hash)]
    else:
        data_type = d["data_type"]
        payload = d["data"]
    model_class = resolve(data_type)
    return data_type, model_class.model_validate(migrate_payload(data_type, payload))


def operation_from_dict(
    d: dict[str, Any], blobs: dict[str, dict[str, Any]] | None = None
) -> Operation:
    op_type = d["type"]
    if op_type == "create":
        data_type, data = _payload_from_dict(d, blobs)
        return Create(
            data=data,
            id=d.get("id"),
            artifact_id=d.get("artifact_id"),
            data_type=data_type,
        )
    elif op_type == "update":
        data_type, new_data = _payload_from_dict(d, blobs)
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
