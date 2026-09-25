from __future__ import annotations

import importlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    ARTIFACT_CREATED = "artifact_created"
    ARTIFACT_UPDATED = "artifact_updated"
    ARTIFACT_DELETED = "artifact_deleted"
    ARTIFACT_STALE = "artifact_stale"


class Event:
    """A lightweight event referencing an artifact by id and type."""

    def __init__(
        self,
        type: EventType,
        artifact_type: type | str,
        artifact_id: str,
    ):
        self.type = type
        self.artifact_type = artifact_type
        self.artifact_id = artifact_id
        self.timestamp = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Serializes the event for durable persistence.

        `artifact_type` may be a class (importable by qualified name) or a
        plain string; both round-trip through `from_dict`. Mirrors the
        qualified-name convention `operations.operation_from_dict` uses for
        artifact payloads.
        """
        if isinstance(self.artifact_type, type):
            type_name = (
                f"{self.artifact_type.__module__}.{self.artifact_type.__qualname__}"
            )
        else:
            type_name = str(self.artifact_type)
        return {
            "type": self.type.value,
            "artifact_type": type_name,
            "artifact_id": self.artifact_id,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        name = d["artifact_type"]
        artifact_type: type | str
        try:
            module_name, _, class_name = name.rpartition(".")
            artifact_type = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError, ValueError):
            # A type that isn't importable in this process (e.g. defined in
            # `__main__`) falls back to its name: matching by `Consume(Type)`
            # won't bind it, but the queue still round-trips instead of
            # refusing to load.
            artifact_type = name
        event = cls(
            type=EventType(d["type"]),
            artifact_type=artifact_type,
            artifact_id=d["artifact_id"],
        )
        timestamp = d.get("timestamp")
        if timestamp is not None:
            event.timestamp = datetime.fromisoformat(timestamp)
        return event

    def __repr__(self) -> str:
        type_name = (
            self.artifact_type.__name__
            if isinstance(self.artifact_type, type)
            else self.artifact_type
        )
        return f"<Event {self.type.value} {type_name} id={self.artifact_id}>"
