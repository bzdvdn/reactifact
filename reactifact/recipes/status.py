"""recipes — deterministic artifact status lifecycles (§67, §69)."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel

from ..artifacts import Artifact, ArtifactType
from ..context import Context
from ..effects import Effects
from ..step import Step

StatusT = TypeVar("StatusT", bound=BaseModel)
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class StatusState:
    """The scoped snapshot `StatusMachine.next_status()` decides from.

    `inputs` is the step's consumed artifacts restricted to `key` (via the
    machine's `query_id_field`), so `all(SourceRef)` means "this turn's
    refs", not "every ref in the context" — no hand-written `query_id == key`
    filters in every `next_status`.
    """

    context: Context
    key: str
    target: Artifact[Any]
    inputs: tuple[Artifact[Any], ...] = ()

    def all(self, artifact_type: type[T]) -> list[Artifact[T]]:
        """Every scoped input whose data is an instance of `artifact_type`."""
        return [
            cast(Artifact[T], artifact)
            for artifact in self.inputs
            if isinstance(artifact.data, artifact_type)
        ]

    def any(self, artifact_type: type[Any]) -> bool:
        """Whether at least one scoped input is an instance of `artifact_type`."""
        return any(isinstance(artifact.data, artifact_type) for artifact in self.inputs)

    def latest(self, artifact_type: type[T]) -> Artifact[T] | None:
        """The last scoped input of `artifact_type`, or `None`."""
        found = self.all(artifact_type)
        return found[-1] if found else None


class StatusMachine(Step[StatusT], Generic[StatusT]):
    """Deterministic `status` lifecycle for an artifact type (§67).

    Subclass it, set `artifact_type`/`terminal`, implement `next_status` (and
    override `owner_key` or tweak `query_id_field`/`status_field` when the
    artifact uses a different key/status column). The runtime advances the
    lifecycle in reaction to events — no manual transition graph (§21, §24).

    `next_status` receives a `StatusState`: the lifecycle target plus this
    step's inputs already restricted to the same correlation key. That keeps
    the transition function a pure `state -> status | None` without the
    repeated `context.list_artifacts(T)` + `query_id == key` filtering.
    """

    artifact_type: ArtifactType | None = None
    terminal: frozenset[str] = frozenset()
    #: Which field of the artifact data carries the lifecycle key / status.
    query_id_field: str = "query_id"
    status_field: str = "status"

    def owner_key(self, artifact: Artifact[Any]) -> str | None:
        """Which lifecycle the artifact belongs to (default: its `query_id_field`)."""
        data = artifact.data
        if isinstance(data, BaseModel):
            key = getattr(data, self.query_id_field, None)
            if isinstance(key, str):
                return key
        return artifact.id

    @abstractmethod
    def next_status(self, state: StatusState) -> str | None:
        """The status the lifecycle should move to, or None (no change)."""

    def on_transition(
        self, context: Context, key: str, old_status: str, new_status: str
    ) -> None:
        """Hook called right before a transition (progress announces, §53)."""

    async def run(
        self,
        context: Context,
        effects: Effects,
        trigger: Artifact[Any] | None,
        inputs: list[Artifact[Any]],
    ) -> None:
        artifact = trigger
        if artifact is None:
            return None
        key = self.owner_key(artifact)
        if key is None:
            return None
        targets = [
            t
            for t in context.list_artifacts(self.artifact_type)
            if self.owner_key(t) == key
        ]
        if not targets:
            return None
        target = targets[0]
        current = getattr(target.data, self.status_field, None)
        if current in self.terminal:
            return None
        state = StatusState(
            context=context,
            key=key,
            target=target,
            inputs=tuple(
                a for a in inputs if getattr(a.data, self.query_id_field, None) == key
            ),
        )
        expected = self.next_status(state)
        if expected is None or expected == current:
            return None
        self.on_transition(context, key, str(current), expected)
        effects.update(target, **{self.status_field: expected})
        return None
