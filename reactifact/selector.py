"""Shared artifact/event selection for `Read` and `Consume`."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Self

from .artifacts import Artifact, ArtifactType
from .context import Context
from .events import EventType


class Selector:
    """Selects artifacts by type, condition and lifecycle event.

    `Read` and `Consume` share this part. `Read` adds `required`; `Consume`
    adds `wakes`/`debounce` and turns the selection into `Trigger`s.
    """

    artifact_type: ArtifactType | None = None
    condition: Callable[[Artifact[Any]], bool] | None = None
    event_types: Sequence[EventType] = (
        EventType.ARTIFACT_CREATED,
        EventType.ARTIFACT_UPDATED,
    )
    include_subclasses: bool = False

    def __init__(
        self,
        artifact_type: ArtifactType | None = None,
        condition: Callable[[Artifact[Any]], bool] | None = None,
        event_types: Sequence[EventType] | None = None,
        *,
        include_subclasses: bool | None = None,
    ) -> None:
        self.artifact_type = artifact_type or self.__class__.artifact_type
        if self.artifact_type is None:
            raise ValueError(
                "artifact_type must be provided either as a class attribute "
                "or a constructor argument"
            )
        self.condition = (
            condition if condition is not None else self.__class__.condition
        )
        self.event_types = list(
            event_types if event_types is not None else self.__class__.event_types
        )
        self.include_subclasses = (
            include_subclasses
            if include_subclasses is not None
            else self.__class__.include_subclasses
        )

    def matches(self, artifact: Artifact[Any]) -> bool:
        """Whether an artifact satisfies this selector's condition."""
        return self.condition is None or self.condition(artifact)

    def collect(self, context: Context) -> list[Artifact[Any]]:
        """Artifacts matching this selector, in context order."""
        return [
            artifact
            for artifact in context.list_artifacts(self.artifact_type)
            if self.matches(artifact)
        ]

    @classmethod
    def on_create(cls, artifact_type: ArtifactType, **kwargs: Any) -> Self:
        return cls(
            artifact_type,
            event_types=(EventType.ARTIFACT_CREATED,),
            **kwargs,
        )

    @classmethod
    def on_update(cls, artifact_type: ArtifactType, **kwargs: Any) -> Self:
        return cls(
            artifact_type,
            event_types=(EventType.ARTIFACT_UPDATED,),
            **kwargs,
        )

    @classmethod
    def on_stale(cls, artifact_type: ArtifactType, **kwargs: Any) -> Self:
        return cls(
            artifact_type,
            event_types=(EventType.ARTIFACT_STALE,),
            **kwargs,
        )

    @classmethod
    def by_field(
        cls,
        artifact_type: ArtifactType,
        field: str,
        value: Any,
        **kwargs: Any,
    ) -> Self:
        return cls(
            artifact_type,
            condition=lambda artifact: getattr(artifact.data, field, None) == value,
            **kwargs,
        )

    @classmethod
    def by_status(
        cls,
        artifact_type: ArtifactType,
        status: str,
        **kwargs: Any,
    ) -> Self:
        return cls.by_field(artifact_type, "status", status, **kwargs)

    @classmethod
    def where(
        cls,
        artifact_type: ArtifactType,
        predicate: Callable[[Artifact[Any]], bool],
        **kwargs: Any,
    ) -> Self:
        return cls(artifact_type, condition=predicate, **kwargs)


__all__ = ["Selector"]
