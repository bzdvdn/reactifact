"""Lifecycle-specific consume variants.

`Consume` listens to CREATED and UPDATED by default. These subclasses narrow
that to one lifecycle event while keeping `condition=`, `wakes=`, `debounce=`
and `include_subclasses=` from the base class.
"""

from __future__ import annotations

from ..events import EventType
from .base import Consume


class OnCreate(Consume):
    """Wake only for a newly created artifact.

    A new id via `create`/`upsert`/`Emit` counts. An existing id through
    `upsert`/`Emit(id=...)` is an update, and `create_once`/`Ensure` on an
    existing id emits no event at all.
    """

    event_types = (EventType.ARTIFACT_CREATED,)


class OnUpdate(Consume):
    """Wake only when an existing artifact changes.

    `effects.update`, `upsert`/`Emit` with an existing id and `Context.touch`
    all produce this event; updating with identical data emits no event.
    """

    event_types = (EventType.ARTIFACT_UPDATED,)


class OnDelete(Consume):
    """Wake when an artifact is deleted.

    The data is gone: use `event: Event` or `trigger: Artifact[Any] | None`
    in `run()`, not a `T` parameter — `collect()` cannot return the deleted
    artifact.
    """

    event_types = (EventType.ARTIFACT_DELETED,)


class OnStale(Consume):
    """Wake when a dependency of this artifact gets a newer version."""

    event_types = (EventType.ARTIFACT_STALE,)


__all__ = ["OnCreate", "OnDelete", "OnStale", "OnUpdate"]
