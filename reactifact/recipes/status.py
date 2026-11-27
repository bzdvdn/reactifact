"""recipes — deterministic artifact status lifecycles (§67, §69)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast, get_args, get_origin

from pydantic import BaseModel

from ..artifacts import Artifact, ArtifactType
from ..consume import Consume
from ..context import Context
from ..step import Edit, Step

StatusT = TypeVar("StatusT", bound=BaseModel)
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class StatusState(Generic[StatusT]):
    """The scoped snapshot `StatusMachine.next_status()` decides from.

    `inputs` is the step's consumed artifacts restricted to `key` via the
    machine's `owner_key`, so `all(SourceRef)` means "this turn's refs", not
    "every ref in the context" — no hand-written `query_id == key` filters in
    every `next_status`.
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


NextStatus = Callable[[StatusState[Any]], str | None]
TransitionHook = Callable[[Context, str, str, str], None]


class StatusMachine(Step[StatusT], Generic[StatusT]):
    """Deterministic `status` lifecycle for an artifact type (§67).

    Build one either with a constructor callback:

        machine = StatusMachine(
            artifact_type=ResearchTurn,
            terminal={"answered", "insufficient"},
            next_status=lambda state: "answered" if state.any(Answer) else None,
        )

    or by subclassing (the generic supplies `artifact_type`):

        class EvaluateTurn(StatusMachine[ResearchTurn]):
            def next_status(self, state): ...

    `next_status` receives a `StatusState`: the lifecycle target plus this
    step's inputs already restricted to the same correlation key. `owner_key`
    can be overridden when the artifact uses a different key than
    `query_id_field`; the same hook scopes the inputs.
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

    def __init__(
        self,
        artifact_type: type[StatusT] | None = None,
        *,
        next_status: NextStatus | None = None,
        terminal: Iterable[str] | None = None,
        consumes: Consume
        | ArtifactType
        | Sequence[Consume | ArtifactType]
        | None = None,
        query_id_field: str = "query_id",
        status_field: str = "status",
        on_transition: TransitionHook | None = None,
        name: str | None = None,
    ) -> None:
        resolved = artifact_type or type(self).artifact_type
        if resolved is None:
            raise TypeError(
                "StatusMachine needs artifact_type=... or a StatusMachine[Model] "
                "subclass"
            )
        self._next_status = next_status
        self._on_transition = on_transition
        self.terminal = (
            frozenset(terminal)
            if terminal is not None
            else frozenset(getattr(type(self), "terminal", ()))
        )
        self.query_id_field = query_id_field
        self.status_field = status_field
        # A status machine updates its target; it does not create it. Keep
        # `artifact_type` for target selection and viz, but declare no creates.
        super().__init__(produces=(), consumes=consumes, name=name)
        self.artifact_type = resolved

    @classmethod
    def from_funcs(
        cls,
        *,
        artifact_type: type[StatusT],
        next_status: NextStatus,
        terminal: Iterable[str] = (),
        on_transition: TransitionHook | None = None,
        query_id_field: str = "query_id",
        status_field: str = "status",
        consumes: Consume
        | ArtifactType
        | Sequence[Consume | ArtifactType]
        | None = None,
        name: str | None = None,
    ) -> StatusMachine[StatusT]:
        """Build a machine from pure callables instead of a subclass."""
        return cls(
            artifact_type=artifact_type,
            next_status=next_status,
            terminal=terminal,
            on_transition=on_transition,
            query_id_field=query_id_field,
            status_field=status_field,
            consumes=consumes,
            name=name,
        )

    def owner_key(self, artifact: Artifact[Any]) -> str | None:
        """Which lifecycle the artifact belongs to (default: its `query_id_field`)."""
        data = artifact.data
        if isinstance(data, BaseModel):
            key = getattr(data, self.query_id_field, None)
            if isinstance(key, str):
                return key
        return artifact.id

    def next_status(self, state: StatusState[StatusT]) -> str | None:
        """The status the lifecycle should move to, or None (no change)."""
        if self._next_status is None:
            raise NotImplementedError(
                f"{type(self).__name__} must implement next_status or pass "
                "next_status=..."
            )
        return self._next_status(state)

    def on_transition(
        self, context: Context, key: str, old_status: str, new_status: str
    ) -> None:
        """Hook called right before a transition (progress announces, §53)."""
        if self._on_transition is not None:
            self._on_transition(context, key, old_status, new_status)

    async def run(
        self,
        context: Context,
        trigger: Artifact[Any] | None,
        inputs: list[Artifact[Any]],
    ) -> Edit | None:
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
            inputs=tuple(a for a in inputs if self.owner_key(a) == key),
            status_field=self.status_field,
        )
        current = state.status
        if current in self.terminal:
            return None
        expected = self.next_status(state)
        if expected is None or expected == current:
            return None
        self.on_transition(context, key, str(current), expected)
        fields: dict[str, Any] = {self.status_field: expected}
        return Edit(target, **fields)
