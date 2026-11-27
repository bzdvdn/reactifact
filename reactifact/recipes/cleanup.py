"""recipes.cleanup — delete a turn's "scratch" artifacts once it's terminal (§24, §42).

A recurring shape across multi-stage flows: several intermediate artifacts
are correlated by some key (a turn's `query_id`, a thread id, ...), never
read again once a terminal artifact exists for that key, and exist only to
keep each stage's output typed and inspectable in between. Left alone,
`Context` (and anything that checkpoints it wholesale, e.g. `SessionStore`)
grows without bound as more turns/threads accumulate. Every app that has hit
this has hand-rolled the same `Step[Terminal]` subclass: react to the
terminal artifact, recover its correlation key, delete each scratch id if
present. `EphemeralCleanup` (and `PrefixedEphemeralCleanup` for the common
`f"{prefix}:{key}"` id shape, §42's own stable-id idiom) is that subclass,
written once.

What this does *not* solve: deciding *whether* a new artifact type belongs
in a given cleanup's scratch list is still the app's call, made once, when
that artifact type is introduced — this recipe only removes the busywork of
re-deriving each id and re-typing the deletion loop by hand.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ..artifacts import Artifact, ArtifactType
from ..consume import Consume, OnCreate
from ..context import Context
from ..step import Drop, Step
from ..step.annotations import Scope

TerminalT = TypeVar("TerminalT", bound=BaseModel)


class EphemeralCleanup(Step[TerminalT], Generic[TerminalT]):
    """Deletes a set of "scratch" artifacts once a terminal artifact exists
    for their shared correlation key.

    Subclass it, set `artifact_type` to the terminal type that triggers
    cleanup, and implement `correlation_of`/`scratch_ids`. Runs once per
    terminal artifact created; each candidate id is deleted only if present
    (idempotent — a scratch artifact that was never produced this turn, or
    was already cleaned up, is silently skipped, not an error).

    Never itself creates an artifact — `artifact_type` only satisfies
    `Step`'s constructor and names what triggers this. `Runtime` only
    validates declared types against `Create` operations, never `Delete`, so
    this produce needs no `also_creates` and never widens anything.
    """

    artifact_type: ArtifactType | None = None

    @abstractmethod
    def correlation_of(self, terminal_artifact_id: str) -> str | None:
        """The key tying the terminal artifact back to its scratch siblings
        (e.g. the turn's `query_id`) — `None` skips cleanup for this
        artifact (e.g. it doesn't carry the expected id shape)."""

    @abstractmethod
    def scratch_ids(self, correlation_id: str) -> Sequence[str]:
        """Every scratch artifact id derived from `correlation_id`, however
        the app builds ids — e.g. `f"{prefix}:{correlation_id}"` for each of
        a fixed list of stage prefixes (see `PrefixedEphemeralCleanup` for
        that exact shape pre-built)."""

    async def run(
        self, context: Context, trigger: Artifact[Any] | None
    ) -> list[Drop] | None:
        terminal = trigger
        if terminal is None:
            return None
        correlation_id = self.correlation_of(terminal.id)
        if not correlation_id:
            return None
        return [
            Drop(artifact_id)
            for artifact_id in self.scratch_ids(correlation_id)
            if context.get(artifact_id) is not None
        ]


class ScopedCleanup(Step):
    """Constructor-style cleanup for artifacts that share a scope, not ids.

    The common chat shape: a terminal `Answer` carries `query_id`, every
    intermediate artifact carries the same `query_id`, and their ids are
    arbitrary. `ScopedCleanup(Answer, scratch_types=(...,), scope=TURN_SCOPE)`
    reacts to `OnCreate(Answer)` and drops every scratch artifact in that
    turn, leaving other turns untouched. It declares `produces=()` and the
    real `deletes` types, so contracts do not claim the cleanup creates an
    `Answer`.

    `scope` is required (pass `None` explicitly for a deliberate global
    cleanup); `consume=` defaults to `OnCreate(terminal_type)`.
    """

    def __init__(
        self,
        terminal_type: type[BaseModel],
        *,
        scratch_types: Sequence[type[BaseModel]],
        scope: Scope | str | None,
        consume: Consume | None = None,
        name: str = "cleanup",
    ) -> None:
        self.terminal_type = terminal_type
        self.scratch_types = tuple(scratch_types)
        if not self.scratch_types:
            raise ValueError("ScopedCleanup needs at least one scratch type")
        super().__init__(
            produces=(),
            deletes=self.scratch_types,
            consumes=consume or OnCreate(terminal_type),
            scope=scope,
            name=name,
        )

    async def run(self, trigger: Artifact[Any] | None) -> list[Drop] | None:
        if trigger is None:
            return None
        return [
            Drop(artifact)
            for artifact_type in self.scratch_types
            for artifact in self.related_all(artifact_type)
            if artifact.id != trigger.id
        ]


class PrefixedEphemeralCleanup(EphemeralCleanup[TerminalT], Generic[TerminalT]):
    """`EphemeralCleanup` for the common id shape: every scratch artifact's
    id is `f"{prefix}:{correlation_id}"` for some fixed list of prefixes (the
    stable-id idiom §42 itself uses as an example, e.g. `f"steer:{qid}:{round}"`
    in `effects.py`). Set `scratch_prefixes` and implement `correlation_of`;
    `scratch_ids` is derived for you.
    """

    scratch_prefixes: tuple[str, ...] = ()

    def scratch_ids(self, correlation_id: str) -> Sequence[str]:
        return [f"{prefix}:{correlation_id}" for prefix in self.scratch_prefixes]


__all__ = ["EphemeralCleanup", "PrefixedEphemeralCleanup", "ScopedCleanup"]
