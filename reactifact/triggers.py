from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .artifacts import Artifact
from .context import Context
from .events import Event, EventType


class Trigger:
    """Condition for launching an agent."""

    def __init__(
        self,
        event_type: EventType,
        artifact_type: type | None = None,
        condition: Callable[[Artifact[Any]], bool] | None = None,
    ):
        self.event_type = event_type
        self.artifact_type = artifact_type
        self.condition = condition

    def matches(self, event: Event, context: Context | None = None) -> bool:
        """Checks whether the event matches this trigger.
        If a condition is set, requires a context to fetch the artifact."""
        if event.type != self.event_type:
            return False
        if self.artifact_type is not None and event.artifact_type != self.artifact_type:
            return False
        if self.condition is not None:
            if context is None:
                raise ValueError("Context is required to evaluate condition")
            artifact = context.get(event.artifact_id)
            if artifact is None:
                return False  # the artifact may have been deleted
            return self.condition(artifact)
        return True

    def __repr__(self) -> str:
        return f"<Trigger {self.event_type.value} {self.artifact_type.__name__ if self.artifact_type else '*'}>"
