"""Correlated and idempotent consume variants."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..artifacts import Artifact, ArtifactType
from ..context import Context
from ..events import EventType
from ..triggers import Trigger
from .base import Consume


class IdempotentConsume(Consume):
    """Wake on `artifact_type` only while the derived target id is absent.

    The declarative form of the common idempotency guard:

        if context.get(f"draft:{topic.id}") is not None:
            return None

    becomes:

        consumes = (IdempotentConsume(Topic, id_of=lambda topic: f"draft:{topic.id}"),)

    `id_of` receives the triggering `Artifact` (id + data), so a key may use
    either. Returning `None` means "no target for this artifact" — it never
    wakes and is excluded from `collect()`, mirroring `AbsentConsume`'s
    unblocked-only inputs.
    """

    def __init__(
        self,
        artifact_type: ArtifactType,
        id_of: Callable[[Artifact[Any]], str | None],
        condition: Callable[[Artifact[Any]], bool] | None = None,
        event_types: Sequence[EventType] | None = None,
        *,
        wakes: bool | None = None,
        debounce: bool | None = None,
        include_subclasses: bool | None = None,
    ):
        if not callable(id_of):
            raise TypeError("IdempotentConsume id_of must be callable")
        super().__init__(
            artifact_type,
            condition,
            event_types,
            wakes=wakes,
            debounce=debounce,
            include_subclasses=include_subclasses,
        )
        self.id_of = id_of

    def _target_id(self, artifact: Artifact[Any]) -> str | None:
        return self.id_of(artifact)

    def to_triggers(self) -> list[Trigger]:
        if not self.wakes:
            return []

        def _matches(artifact: Artifact[Any], context: Context) -> bool:
            if self.condition is not None and not self.condition(artifact):
                return False
            target = self._target_id(artifact)
            return target is not None and context.get(target) is None

        return [
            Trigger(
                event_type,
                self.artifact_type,
                context_condition=_matches,
                debounce=self.debounce,
                include_subclasses=self.include_subclasses,
            )
            for event_type in self.event_types
        ]

    def collect(self, context: Context) -> list[Artifact[Any]]:
        return [
            artifact
            for artifact in super().collect(context)
            if (target := self._target_id(artifact)) is not None
            and context.get(target) is None
        ]


class CorrelatedConsume(Consume):
    """Fires (and feeds inputs) only for a correlation key (by `key`) where
    every type in `require` is present and every type in `forbid` is
    absent — the shared mechanism behind `JoinConsume` (`forbid=()`) and
    `AbsentConsume` (`require` of one type, `forbid` of another), unified
    here because both turned out to be the same "group by key, check who's
    in the group" scan, just with a different pass/fail rule. Written
    directly against this when a case needs *both* at once — "an approved
    `PendingQuestion` and its `Report` exist, but no `HelpdeskTicket` for
    that thread yet" — which neither `JoinConsume` nor `AbsentConsume` alone
    can express without nesting one inside the other.

    ```python
    consumes = [
        CorrelatedConsume(
            key=lambda d: d.thread_id,
            require=[Report, PendingQuestion],
            require_conditions={PendingQuestion: lambda d: d.answered},
            forbid=[HelpdeskTicket],
        ),
    ]
    ```

    `key` takes an artifact's `.data` (not the `Artifact` wrapper); every
    type in `require`/`forbid` needs to expose whatever it reads.
    `require_conditions`/`forbid_conditions`, if given, filter candidates of
    a specific type before they can count toward completing/blocking a
    group (e.g. only an *answered* `PendingQuestion` completes the group;
    only a *sent* notification blocks). Listens to CREATED/UPDATED events on
    every `require` type (so the agent wakes up regardless of which one
    completes the group last) — not on `forbid` types, matching
    `AbsentConsume`'s original documented choice not to re-fire when a
    blocking instance is later deleted.

    `collect()` returns every `require` type's artifact from each group that
    is both complete and unblocked, in `context.list_artifacts()` order — a
    produce reads them by `isinstance`, the same way it would read a mixed
    `inputs` list from several ordinary `Consume`s.
    """

    def __init__(
        self,
        *,
        key: Callable[[Any], Any],
        require: Sequence[ArtifactType],
        require_conditions: dict[ArtifactType, Callable[[Any], bool]] | None = None,
        forbid: Sequence[ArtifactType] = (),
        forbid_conditions: dict[ArtifactType, Callable[[Any], bool]] | None = None,
        event_types: Sequence[EventType] = (
            EventType.ARTIFACT_CREATED,
            EventType.ARTIFACT_UPDATED,
        ),
        wakes: bool = True,
        debounce: bool = False,
    ):
        if not require:
            raise ValueError(
                "CorrelatedConsume needs at least one required artifact type"
            )
        self.key = key
        self.require = tuple(require)
        self.require_conditions = dict(require_conditions or {})
        self.forbid = tuple(forbid)
        self.forbid_conditions = dict(forbid_conditions or {})
        # Base Consume fields exist for isinstance/API compatibility
        # (`Agent._validate_contracts`'s `isinstance(c, Consume)` check) —
        # `artifact_type` is nominally the first required type;
        # `to_triggers()`/`collect()` below never actually consult it.
        super().__init__(
            self.require[0], event_types=event_types, wakes=wakes, debounce=debounce
        )

    def _passes_require(self, part_type: ArtifactType, artifact: Artifact[Any]) -> bool:
        condition = self.require_conditions.get(part_type)
        return condition is None or condition(artifact.data)

    def _groups(self, context: Context) -> dict[Any, dict[ArtifactType, Artifact[Any]]]:
        groups: dict[Any, dict[ArtifactType, Artifact[Any]]] = {}
        for part_type in self.require:
            for artifact in context.list_artifacts(part_type):
                if not self._passes_require(part_type, artifact):
                    continue
                groups.setdefault(self.key(artifact.data), {})[part_type] = artifact
        return groups

    def _blocked(self, k: Any, context: Context) -> bool:
        for forbidden_type in self.forbid:
            condition = self.forbid_conditions.get(forbidden_type)
            for artifact in context.list_artifacts(forbidden_type):
                if self.key(artifact.data) != k:
                    continue
                if condition is None or condition(artifact.data):
                    return True
        return False

    def _group_ready(self, k: Any, context: Context) -> bool:
        group = self._groups(context).get(k, {})
        if not all(part_type in group for part_type in self.require):
            return False
        return not self._blocked(k, context)

    def _make_condition(
        self, part_type: ArtifactType
    ) -> Callable[[Artifact[Any], Context], bool]:
        """A real closure (not an in-loop lambda) so each trigger's
        `part_type` is pinned to the value it was built for — a lambda
        defined directly inside the `to_triggers()` loop would instead see
        whatever `part_type` ends up as after the loop finishes (Python's
        late-binding closures over a loop variable)."""

        def condition(artifact: Artifact[Any], context: Context) -> bool:
            return self._passes_require(part_type, artifact) and self._group_ready(
                self.key(artifact.data), context
            )

        return condition

    def to_triggers(self) -> list[Trigger]:
        if not self.wakes:
            return []
        triggers = []
        for part_type in self.require:
            condition = self._make_condition(part_type)
            for event_type in self.event_types:
                triggers.append(
                    Trigger(
                        event_type,
                        part_type,
                        context_condition=condition,
                        debounce=self.debounce,
                    )
                )
        return triggers

    def collect(self, context: Context) -> list[Artifact[Any]]:
        result: list[Artifact[Any]] = []
        for k, group in self._groups(context).items():
            if len(group) == len(self.require) and not self._blocked(k, context):
                result.extend(group.values())
        return result


def JoinConsume(
    *parts: ArtifactType,
    key: Callable[[Any], Any],
    part_conditions: dict[ArtifactType, Callable[[Any], bool]] | None = None,
    event_types: Sequence[EventType] = (
        EventType.ARTIFACT_CREATED,
        EventType.ARTIFACT_UPDATED,
    ),
    wakes: bool = True,
    debounce: bool = False,
) -> CorrelatedConsume:
    """`CorrelatedConsume(require=parts)` — fires (and feeds inputs) only
    once artifacts of *every* type in `parts` exist, correlated by `key`,
    e.g. an approval gate waiting on a `Report` and its `PendingQuestion` by
    `thread_id`. See `CorrelatedConsume`'s docstring for the mechanism and
    why this is a thin factory over it, not its own class.
    """
    if len(parts) < 2:
        raise ValueError("JoinConsume needs at least two artifact types to join")
    return CorrelatedConsume(
        key=key,
        require=parts,
        require_conditions=part_conditions,
        event_types=event_types,
        wakes=wakes,
        debounce=debounce,
    )


def AbsentConsume(
    artifact_type: ArtifactType,
    *,
    absent_type: ArtifactType,
    key: Callable[[Any], Any],
    absent_condition: Callable[[Any], bool] | None = None,
    condition: Callable[[Any], bool] | None = None,
    event_types: Sequence[EventType] = (
        EventType.ARTIFACT_CREATED,
        EventType.ARTIFACT_UPDATED,
    ),
    wakes: bool = True,
    debounce: bool = False,
) -> CorrelatedConsume:
    """`CorrelatedConsume(require=[artifact_type], forbid=[absent_type])` —
    `JoinConsume`'s mirror image: fires (and feeds inputs) only for
    artifacts of `artifact_type` that have *no* matching `absent_type` yet,
    correlated by `key` — the declarative form of the create_once/id-guard
    idempotency check a produce would otherwise hand-roll to avoid a
    duplicate side effect (don't file a `HelpdeskTicket` for a thread that
    already has one). `condition` (on `artifact_type`, unlike
    `part_conditions` above — note it takes `.data`, not the `Artifact`)
    and `absent_condition` (on `absent_type`) map onto
    `CorrelatedConsume`'s `require_conditions`/`forbid_conditions`. See
    `CorrelatedConsume`'s docstring for the mechanism and for why this only
    reacts to `artifact_type`'s own events, not `absent_type`'s.
    """
    return CorrelatedConsume(
        key=key,
        require=[artifact_type],
        require_conditions={artifact_type: condition}
        if condition is not None
        else None,
        forbid=[absent_type],
        forbid_conditions={absent_type: absent_condition}
        if absent_condition is not None
        else None,
        event_types=event_types,
        wakes=wakes,
        debounce=debounce,
    )


__all__ = [
    "AbsentConsume",
    "CorrelatedConsume",
    "IdempotentConsume",
    "JoinConsume",
]
