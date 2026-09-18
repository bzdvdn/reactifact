"""recipes — deterministic artifact status lifecycles (§67, §69)."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ..artifacts import Artifact
from ..context import Context
from ..produce import Produce, ProduceCall

StatusT = TypeVar("StatusT", bound=BaseModel)


class StatusMachine(Produce[StatusT], Generic[StatusT]):
    """Deterministic `status` lifecycle for an artifact type (§67).

    Subclass it, set `artifact_type`/`terminal`, implement `next_status` (and
    override `owner_key` or tweak `query_id_field`/`status_field` when the
    artifact uses a different key/status column). The runtime advances the
    lifecycle in reaction to events — no manual transition graph (§21, §24).
    """

    artifact_type: type[StatusT] | None = None
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
    def next_status(self, context: Context, key: str) -> str | None:
        """The status the lifecycle should move to, or None (no change)."""

    def on_transition(
        self, context: Context, key: str, old_status: str, new_status: str
    ) -> None:
        """Hook called right before a transition (progress announces, §53)."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        artifact = call.trigger
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
        expected = self.next_status(context, key)
        if expected is None or expected == current:
            return None
        self.on_transition(context, key, str(current), expected)
        self.effects.update(target, **{self.status_field: expected})
        return None
