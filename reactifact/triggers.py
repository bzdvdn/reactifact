from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .artifacts import Artifact
from .context import Context
from .events import Event, EventType


class Trigger:
    """Condition for launching an agent.

    Two shapes of condition, both optional and combinable with each other:

    - `condition(artifact) -> bool` — the common case, evaluated on just the
      artifact the event is about (what `Consume.condition` compiles to).
    - `context_condition(artifact, context) -> bool` — for a condition that
      needs to look at *other* artifacts too (a join/correlation across
      types — see `reactifact.consume.JoinConsume`), since `condition`
      alone has no way to reach the rest of `Context` from a plain
      per-artifact callable defined once, before any specific `Context`
      exists.

    `debounce` (default `False`): a hint to `Runtime` — when several events
    in the same generation would each independently satisfy this trigger for
    the same agent, collapse them into a single run instead of one per
    event (see `Runtime._arun_once_impl` and `Consume`'s own `debounce`
    param, which is what actually sets this in practice). `Trigger` itself
    only carries the flag; it plays no part in `matches()`.
    """

    def __init__(
        self,
        event_type: EventType,
        artifact_type: type | None = None,
        condition: Callable[[Artifact[Any]], bool] | None = None,
        context_condition: Callable[[Artifact[Any], Context], bool] | None = None,
        debounce: bool = False,
    ):
        self.event_type = event_type
        self.artifact_type = artifact_type
        self.condition = condition
        self.context_condition = context_condition
        self.debounce = debounce

    def matches(self, event: Event, context: Context | None = None) -> bool:
        """Checks whether the event matches this trigger.
        If a condition is set, requires a context to fetch the artifact."""
        if event.type != self.event_type:
            return False
        if self.artifact_type is not None and event.artifact_type != self.artifact_type:
            return False
        if self.condition is not None or self.context_condition is not None:
            if context is None:
                raise ValueError("Context is required to evaluate condition")
            artifact = context.get(event.artifact_id)
            if artifact is None:
                return False  # the artifact may have been deleted
            if self.condition is not None and not self.condition(artifact):
                return False
            if self.context_condition is not None and not self.context_condition(
                artifact, context
            ):
                return False
        return True

    def __repr__(self) -> str:
        return f"<Trigger {self.event_type.value} {self.artifact_type.__name__ if self.artifact_type else '*'}>"
