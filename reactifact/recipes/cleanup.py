"""recipes.cleanup — delete a turn's "scratch" artifacts once it's terminal (§24, §42).

A recurring shape across multi-stage flows: several intermediate artifacts
are correlated by some key (a turn's `query_id`, a thread id, ...), never
read again once a terminal artifact exists for that key, and exist only to
keep each stage's output typed and inspectable in between. Left alone,
`Context` (and anything that checkpoints it wholesale, e.g. `SessionStore`)
grows without bound as more turns/threads accumulate. Every app that has hit
this has hand-rolled the same `Produce[Terminal]` subclass: react to the
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
from typing import Generic, TypeVar

from pydantic import BaseModel

from ..produce import Produce, ProduceCall

TerminalT = TypeVar("TerminalT", bound=BaseModel)


class EphemeralCleanup(Produce[TerminalT], Generic[TerminalT]):
    """Deletes a set of "scratch" artifacts once a terminal artifact exists
    for their shared correlation key.

    Subclass it, set `artifact_type` to the terminal type that triggers
    cleanup, and implement `correlation_of`/`scratch_ids`. Runs once per
    terminal artifact created; each candidate id is deleted only if present
    (idempotent — a scratch artifact that was never produced this turn, or
    was already cleaned up, is silently skipped, not an error).

    Never itself creates an artifact — `artifact_type` only satisfies
    `Produce`'s constructor and names what triggers this. `Runtime` only
    validates declared types against `Create` operations, never `Delete`, so
    this produce needs no `also_creates` and never widens anything.
    """

    artifact_type: type[TerminalT] | None = None

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

    async def produce(self, call: ProduceCall) -> None:
        terminal = call.trigger
        if terminal is None:
            return None
        correlation_id = self.correlation_of(terminal.id)
        if not correlation_id:
            return None
        for artifact_id in self.scratch_ids(correlation_id):
            if call.context.get(artifact_id) is not None:
                self.effects.delete(artifact_id)
        return None


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


__all__ = ["EphemeralCleanup", "PrefixedEphemeralCleanup"]
