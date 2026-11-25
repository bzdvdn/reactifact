"""recipes — deterministic artifact status lifecycles (§67, §69)."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast, get_args, get_origin

from pydantic import BaseModel

from ..artifacts import Artifact, ArtifactType
from ..context import Context
from ..effects import Effects
from ..step import Step

StatusT = TypeVar("StatusT", bound=BaseModel)
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class StatusState(Generic[StatusT]):
    """The scoped snapshot `StatusMachine.next_status()` decides from.

    `inputs` is the step's consumed artifacts restricted to `key` (via the
    machine's `query_id_field`), so `all(SourceRef)` means "this turn's
    refs", not "every ref in the context" — no hand-written `query_id == key`
    filters in every `next_status`.
    """

    context: Context
    key: str
    target: Artifact[StatusT]
    inputs: tuple[Artifact[Any], ...] = ()
    status_field: str = "status"

    @property
    def status(self) -> str | None:
        """The target's current status, read through the machine's field."""
        value = getattr(self.target.data, self.status_field, None)
        return value if isinstance(value, str) else None

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

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.artifact_type is not None:
            return
        for base in getattr(cls, "__orig_bases__", ()):
            if get_origin(base) is StatusMachine:
                args = get_args(base)
                if args and isinstance(args[0], type):
                    cls.artifact_type = args[0]
                    return

    @classmethod
    def from_funcs(
        cls,
        *,
        artifact_type: type[StatusT],
        next_status: Any,
        terminal: frozenset[str] | set[str] = frozenset(),
        on_transition: Any = None,
        query_id_field: str = "query_id",
        status_field: str = "status",
    ) -> StatusMachine[StatusT]:
        """Build a machine from a pure `next_status` function instead of a class.

        `next_status(state) -> str | None`; `on_transition(context, key, old,
        new)` is optional. The artifact type is explicit here because there is
        no subclass generic to infer it from.
        """
        return _FuncStatusMachine(
            artifact_type=artifact_type,
            next_status_fn=next_status,
            terminal=terminal,
            on_transition_fn=on_transition,
            query_id_field=query_id_field,
            status_field=status_field,
        )

    def owner_key(self, artifact: Artifact[Any]) -> str | None:
        """Which lifecycle the artifact belongs to (default: its `query_id_field`)."""
        data = artifact.data
        if isinstance(data, BaseModel):
            key = getattr(data, self.query_id_field, None)
            if isinstance(key, str):
                return key
        return artifact.id

    @abstractmethod
    def next_status(self, state: StatusState[StatusT]) -> str | None:
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
        if self.artifact_type is None:
            raise TypeError(
                f"{type(self).__name__} needs artifact_type=... or StatusMachine[Model]"
            )
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
        target = max(targets, key=lambda item: item.updated_at)
        state: StatusState[StatusT] = StatusState(
            context=context,
            key=key,
            target=cast("Artifact[StatusT]", target),
            inputs=tuple(
                a for a in inputs if getattr(a.data, self.query_id_field, None) == key
            ),
            status_field=self.status_field,
        )
        current = state.status
        if current in self.terminal:
            return None
        expected = self.next_status(state)
        if expected is None or expected == current:
            return None
        self.on_transition(context, key, str(current), expected)
        effects.update(target, **{self.status_field: expected})
        return None


class _FuncStatusMachine(StatusMachine[StatusT]):
    """`StatusMachine.from_funcs` adapter: delegates the pure hooks."""

    def __init__(
        self,
        *,
        artifact_type: type[StatusT],
        next_status_fn: Any,
        terminal: frozenset[str] | set[str],
        on_transition_fn: Any,
        query_id_field: str,
        status_field: str,
    ) -> None:
        super().__init__(artifact_type=artifact_type)
        self.terminal = frozenset(terminal)
        self.query_id_field = query_id_field
        self.status_field = status_field
        self._next_status_fn = next_status_fn
        self._on_transition_fn = on_transition_fn

    def next_status(self, state: StatusState[StatusT]) -> str | None:
        return cast("str | None", self._next_status_fn(state))

    def on_transition(
        self, context: Context, key: str, old_status: str, new_status: str
    ) -> None:
        if self._on_transition_fn is not None:
            self._on_transition_fn(context, key, old_status, new_status)
