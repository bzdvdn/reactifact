"""reactifact.effects — the produce-scoped effect set (§24, §41, §67).

The *authoring surface* of a produce. Instead of hand-assembling a `Patch` on
every turn, a produce writes what should change into its effect slot, and the
runtime compiles the slot into one atomic patch (commit + events + trace):

    class Scout(Produce[SourceRef]):
        async def produce(self, call: ProduceCall) -> None:
            refs = await fan_out_sources(call.context, query, owner_id=...)
            self.effects.create(SearchDone(...), id=f"scouted:{qid}")
            return None

`Effects` is produce-scoped and concurrency-safe: the runtime pushes a fresh
slot via a contextvar before invoking the produce and pops it afterwards, so
parallel produces never see each other's effects. Nothing is applied until the
runtime commits — atomicity stays structural (§41), no diff/rollback.

Users rarely touch `Patch` in an ordinary produce: `Effects` is the language,
`Patch` is the compiled transport. Advanced assembly (recipes, tool loops) can
still build a `Patch` explicitly and return it — the runtime merges both.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from .patches import (
    Create,
    Link,
    Operation,
    Patch,
    Unlink,
    Update,
    _auto_id,
    _id_of,
)

if TYPE_CHECKING:
    from .artifacts import Artifact
    from .context import Context


class Handle:
    """A patch-local handle for a *planned* artifact — link/unlink without ids (§38).

    Returned by `Effects.create`; the id is pinned, so the handle is a valid
    link target (`answer.link("supported_by", evidence)`), and the reader sees
    which artifact a provenance edge comes from.
    """

    __slots__ = ("_effects", "id", "_data")

    def __init__(self, effects: Effects, artifact_id: str, data: Any):
        self._effects = effects
        self.id = artifact_id
        self._data = data

    def link(self, relation: str, target: Any) -> Handle:
        """Appends `Link` from this planned artifact to `target` (id/Artifact/Handle)."""
        self._effects.link(self.id, relation, _id_of(target))
        return self

    def unlink(self, relation: str | None = None, target: Any | None = None) -> Handle:
        self._effects.unlink(
            self.id, relation, _id_of(target) if target is not None else None
        )
        return self

    @property
    def type(self) -> str:
        return type(self._data).__name__

    def __repr__(self) -> str:
        return f"Handle(id={self.id!r}, {self.type})"


class Effects:
    """The current produce's effect set (creates/updates/links to commit once)."""

    __slots__ = ("_context", "operations")

    def __init__(self, context: Context):
        self._context = context
        self.operations: list[Operation] = []

    @property
    def context(self) -> Context:
        return self._context

    def add(self, op: Operation) -> Effects:
        self.operations.append(op)
        return self

    def create(self, data: Any, *, id: str | None = None) -> Handle:
        """Plans a new artifact; returns a linkable/updatable handle (§38).

        If `id` names an artifact that already exists when this effect is
        applied, the runtime treats it as a refresh (a new version of the
        same logical entity, §42/§43) rather than creating a duplicate — the
        same rule `Runtime._apply_patch` documents. Use `upsert` at the call
        site when that "may already exist" intent should be explicit instead
        of implicit in a plain `create(..., id=...)`.
        """
        artifact_id = id or _auto_id(type(data).__name__)
        self.operations.append(Create(data, id=artifact_id))
        return Handle(self, artifact_id, data)

    def create_once(self, data: Any, *, id: str) -> Handle | None:
        """Idempotent create (§42): `None` if `id` already exists in the
        context, otherwise the same as `create(data, id=id)`.

        Folds the "already done" guard every produce needs for a re-derived
        id (`f"answer:{qid}"`) into the call itself:

            handle = self.effects.create_once(Answer(...), id=f"answer:{qid}")
            if handle is None:
                return None  # already answered — nothing to do

        instead of a separate `if context.get(f"answer:{qid}") is not None:
        return None` above the call — one less place to get the id string
        wrong between the guard and the create.
        """
        if self._context.get(id) is not None:
            return None
        return self.create(data, id=id)

    def create_once_from(
        self,
        source: Artifact[Any] | Handle | str,
        data: Any,
        *,
        prefix: str | None = None,
    ) -> Handle | None:
        """Idempotent create whose id is derived from another artifact (§42).

        Folds the single most common way a re-derivable id is built by hand —
        `f"answer:{question.id}"`, `f"review:{pr.id}"` — into the call, so a
        produce doesn't have to invent the id string (nor get it subtly wrong
        between its guard and its `create`):

            answer = self.effects.create_once_from(question, Answer(...))
            if answer is None:
                return None  # already answered this question

        `prefix` defaults to the created model's class name, lowercased
        (`Answer` → `answer:{source_id}`). `source` may be an `Artifact`, an
        effects `Handle`, or a plain id string. `None` back means an artifact
        with the derived id already exists — skip, exactly like `create_once`.
        """
        source_id = _id_of(source)
        artifact_id = f"{prefix or type(data).__name__.lower()}:{source_id}"
        return self.create_once(data, id=artifact_id)

    def upsert(self, data: Any, *, id: str) -> Handle:
        """Explicit create-or-refresh: same effect as `create(data, id=id)`.

        Purely a call-site name: identical to `create(..., id=...)`, but says
        at the call site that a refresh is an expected outcome, not a
        surprise — reach for it when the artifact may already exist (e.g. a
        re-derived id like `f"answer:{qid}"`).
        """
        return self.create(data, id=id)

    def update(self, artifact: Artifact[Any], **fields: Any) -> Effects:
        """Bumps fields of an *existing* artifact (a new version).

        Note the name means something different here than on `Patch.update`/
        `Context.update`/`Artifact.update` (a full data replacement) — this is
        the one intentional exception, matching `Patch.update_fields`
        instead. `Effects` is the everyday authoring surface where "update
        some fields" is the common case (§18 above), so it gets the short
        name; the lower-level, less-used `Patch`/`Context`/`Artifact` surface
        keeps `update` for the operation it's actually named after.
        """
        new_data = artifact.data.model_copy(update=fields)
        self.operations.append(Update(artifact.id, new_data))
        return self

    def delete(self, artifact: Any) -> Effects:
        from .patches import Delete

        self.operations.append(Delete(_id_of(artifact)))
        return self

    def link(self, source: Any, relation: str, target: Any) -> Effects:
        self.operations.append(Link(_id_of(source), relation, _id_of(target)))
        return self

    def ask(
        self,
        question: str,
        *,
        kind: str = "general",
        notes: dict[str, Any] | None = None,
        id: str | None = None,
    ) -> Handle:
        """Poses a question to a human (HITL, §60): creates a `PendingQuestion`.

        Returns a handle you can link later; the human answer is recorded via
        `effects.resume(question_art, resolution)` (§60). Pass `id` for a
        stable, re-derivable question (e.g. `f"steer:{qid}:{round}"`) so a
        guard like `if context.get(id) is not None: return None` can stop the
        produce from asking again while the question is still unanswered.
        """
        from .interrupt import PendingQuestion

        return self.create(
            PendingQuestion(question=question, kind=kind, notes=dict(notes or {})),
            id=id,
        )

    def resume(self, question: Any, resolution: str) -> Effects:
        """Records the human answer on a `PendingQuestion` (HITL, §60)."""
        from datetime import UTC, datetime

        self.update(
            question,
            answered=True,
            resolution=resolution,
            resolved_at=datetime.now(UTC),
        )
        return self

    def unlink(
        self,
        source: Any,
        relation: str | None = None,
        target: Any | None = None,
    ) -> Effects:
        self.operations.append(
            Unlink(
                _id_of(source), relation, _id_of(target) if target is not None else None
            )
        )
        return self

    def is_empty(self) -> bool:
        return len(self.operations) == 0

    def to_patch(self) -> Patch:
        """Compiles the effects into a `Patch` (the runtime's transport)."""
        patch = Patch()
        for op in self.operations:
            patch.add(op)
        return patch


# --------------------------------------------------------------------------- #
# Produce-scoped slot — the runtime pushes/pops a fresh Effects per produce run
# --------------------------------------------------------------------------- #

_ACTIVE: ContextVar[Effects | None] = ContextVar("reactifact_effects", default=None)


def current_effects() -> Effects | None:
    """The produce-scoped effect slot, or None outside a running produce."""
    return _ACTIVE.get()


def set_effects(effects: Effects | None) -> Token[Any]:
    return _ACTIVE.set(effects)


def reset_effects(token: Token[Any]) -> None:
    _ACTIVE.reset(token)


__all__ = ["Effects", "Handle", "current_effects", "reset_effects", "set_effects"]
