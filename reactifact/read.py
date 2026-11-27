"""`Read`: the typed input contract of a `Step`."""

from __future__ import annotations

from typing import Any, Self

from .artifacts import Artifact, ArtifactType
from .consume.base import Consume
from .context import Context
from .events import EventType
from .selector import Selector


class Read(Selector):
    """An artifact input a step needs before it can run.

    `Read` is deliberately not a `Consume`: it never wakes an agent. It only
    says which artifacts the step needs, on which lifecycle events, and whether
    their presence is required. `Agent.consumes` owns wake-ups.
    """

    required: bool = True
    #: Set when this `Read` wraps an advanced `Consume` variant
    #: (`IdempotentConsume`, `JoinConsume`, ...). `to_consume()` returns it.
    _consume: Consume | None = None

    def __init__(
        self,
        artifact_type: ArtifactType | None = None,
        condition: Any | None = None,
        event_types: Any | None = None,
        *,
        required: bool | None = None,
        include_subclasses: bool | None = None,
    ) -> None:
        super().__init__(
            artifact_type,
            condition,
            event_types,
            include_subclasses=include_subclasses,
        )
        self.required = required if required is not None else self.__class__.required

    def to_consume(self) -> Consume:
        """Runtime view of this input; `required` becomes `wakes`."""
        if self._consume is not None:
            return self._consume
        return Consume(
            self.artifact_type,
            self.condition,
            self.event_types,
            wakes=self.required,
            include_subclasses=self.include_subclasses,
        )

    def collect(self, context: Context) -> list[Artifact[Any]]:
        if self._consume is not None:
            return self._consume.collect(context)
        return super().collect(context)

    @classmethod
    def from_consume(cls, consume: Consume) -> Self:
        read = cls(
            consume.artifact_type,
            consume.condition,
            consume.event_types,
            required=consume.wakes,
            include_subclasses=consume.include_subclasses,
        )
        read._consume = consume
        return read

    @classmethod
    def idempotent(cls, *args: Any, **kwargs: Any) -> Self:
        """A read that only matches while its derived target id is absent."""
        from .consume import IdempotentConsume

        return cls.from_consume(IdempotentConsume(*args, **kwargs))

    @classmethod
    def join(cls, *args: Any, **kwargs: Any) -> Self:
        """A read over a correlated group of artifacts."""
        from .consume import JoinConsume

        return cls.from_consume(JoinConsume(*args, **kwargs))

    @classmethod
    def correlated(cls, *args: Any, **kwargs: Any) -> Self:
        """A read over a custom correlated group."""
        from .consume import CorrelatedConsume

        return cls.from_consume(CorrelatedConsume(*args, **kwargs))

    @classmethod
    def after_delete(cls, artifact_type: ArtifactType, **kwargs: Any) -> Self:
        """Run this step after an artifact is deleted.

        Deletion has no live artifact to collect: use `event: Event` or
        `trigger: Artifact[Any] | None` in `run()`, never `T`.
        """
        kwargs.setdefault("required", True)
        return cls(
            artifact_type,
            event_types=(EventType.ARTIFACT_DELETED,),
            **kwargs,
        )


__all__ = ["Read"]
