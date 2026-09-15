"""reactifact.branching — fork/merge semantics and their durable persistence
(§39-§40).

`clone_context`/`fork_context`/`merge_context_from`/`merge_contexts` are the
git-like state operations `Context.clone()`/`.branch()`/`.merge_from()`/
`.merge()` delegate to — moved here so the git-like algorithm lives next to
the concept it implements, not folded into `Context`'s general CRUD/relations/
HITL surface. Application code should call the `Context` methods (see
[docs/en/branching.md](../docs/en/branching.md)); these module-level
functions are exported for building on `Context` without one already in
hand (e.g. `BranchStore`'s own load/merge path below), not as an equally
first-class alternative entry point. `BranchStore` only *persists* named
forks so they survive a restart:

    store = BranchStore(SQLiteKVBackend("sessions.sqlite3"))
    await store.save_branch(ctx_branch, session_id="demo", name="hypothesis-a")
    restored = await store.load_branch("demo", "hypothesis-a")

Each branch key holds the full self-contained context (`to_dict`, which now also
carries the fork base snapshot), so a merged restart keeps `merge()` conflict
detection working. Naming follows `branch:<session>:<name>`; the KV backend stays
the only storage primitive — branches are a convention on top of it, not a new
backend (matching the constitution: semantics live in Context operations).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .artifacts import Artifact
from .checkpoints import KVBackend
from .commit import Commit
from .context import Context
from .patches import Create, Delete, Link, Operation, Update


class MergeConflict(Exception):
    """Two branches changed the same artifact differently since their fork (§40).

    The framework never chooses silently; a verifier or merge policy resolves.
    """

    def __init__(self, message: str, conflicts: list[str] | None = None):
        super().__init__(message)
        self.conflicts = conflicts or []


def clone_context(source: Context) -> Context:
    """Deep copy of `source`'s live state (artifacts, log, relations)."""
    new_ws = Context()
    for artifact in source._artifacts.values():
        new_artifact = Artifact(
            data=artifact.data.model_copy(deep=True),
            id=artifact.id,  # <-- important!
            created_by_commit=artifact.created_by_commit,
        )
        new_artifact._history = [v.model_copy(deep=True) for v in artifact._history]
        new_artifact.created_at = artifact.created_at
        new_artifact.updated_at = artifact.updated_at
        new_ws._artifacts[artifact.id] = new_artifact
    new_ws._log = source._log.copy()
    new_ws._relations = source._relations.copy()
    new_ws._recompute_stale()
    new_ws._reindex_by_type()
    return new_ws


def fork_context(source: Context, *, name: str = "") -> Context:
    """Forks an isolated copy of `source` for alternative state exploration (§39).

    The fork records a snapshot of its base, so a later `merge_contexts` of
    two fork-mates can detect diverged artifacts three-way (§40). The branch
    shares `resources` with the parent but is otherwise fully independent:
    subsequent changes on either side do not affect the other.
    """
    fork = clone_context(source)
    fork.resources = source.resources
    fork._base = clone_context(source)
    fork._fork_name = name
    return fork


def merge_context_from(target: Context, other: Context) -> None:
    """Two-way merge: adopts everything in `other` that is newer/absent in
    `target` — no conflict detection (see `merge_contexts` for that)."""
    operations: list[Operation] = []
    for other_artifact in other.list_artifacts():
        other_id = other_artifact.id
        current = target.get(other_id)
        if current is not None:
            if other_artifact.version > current.version:
                new_data = other_artifact.data.model_copy(deep=True)
                target.update(other_id, new_data)
                operations.append(Update(other_id, new_data))
        else:
            new_data = other_artifact.data.model_copy(deep=True)
            target.create(new_data, id=other_id)
            operations.append(Create(new_data, id=other_id))
    for rel in other.relations():
        if (rel.source_id, rel.relation, rel.target_id) not in target._relations:
            target.link(rel.source_id, rel.relation, rel.target_id)
            operations.append(Link(rel.source_id, rel.relation, rel.target_id))
    if operations:
        target.log_commit(
            Commit(author="merge", message="Merged Context", operations=operations)
        )


def _data_sig(artifact: Artifact[Any] | None) -> Any:
    """Canonical signature of an artifact's current data (None = absent)."""
    if artifact is None:
        return None
    return artifact.data.model_dump(mode="json")


def _kind_short(signature: Any) -> str:
    if signature is None:
        return "absent"
    return "changed"


def merge_contexts(
    target: Context, other: Context, *, message: str = "Merged branch"
) -> None:
    """Merges `other` into `target` with explicit conflicts, atomically (§40).

    Three-way merge against the shared fork base (the fork snapshot of
    `other`, or of `target` when `other` has none). For every artifact that
    exists anywhere among base/target/other:

        equal(target, other)  → no-op
        equal(target, base)   → adopt `other` (only it moved the artifact)
        equal(other, base)    → keep `target` (only it moved the artifact)
        otherwise              → MergeConflict, nothing is applied

    So a change adopted from `other` never silently overwrites a change made
    on `target` since the fork (§40: the framework must not choose silently).
    """
    base = other._base if other._base is not None else target._base
    if base is None:
        base = Context()

    ids = set(base._artifacts) | set(target._artifacts) | set(other._artifacts)
    operations: list[Operation] = []
    pending: dict[str, BaseModel] = {}
    conflicts: list[str] = []

    for artifact_id in sorted(ids):
        base_art = base._artifacts.get(artifact_id)
        target_art = target._artifacts.get(artifact_id)
        other_art = other._artifacts.get(artifact_id)
        sb = _data_sig(base_art)
        st = _data_sig(target_art)
        so = _data_sig(other_art)
        if st == so:
            continue
        if st == sb or so == sb:
            if so == sb:
                continue  # only target moved it — keep as is
            # only other moved it — adopt
            if other_art is None:
                operations.append(Delete(artifact_id))
            else:
                pending[artifact_id] = other_art.data.model_copy(deep=True)
                operations.append(
                    Create(other_art.data)
                    if target_art is None
                    else Update(artifact_id, other_art.data)
                )
        else:
            conflicts.append(
                f"{artifact_id} diverged since the fork "
                f"(target={_kind_short(st)}, other={_kind_short(so)})"
            )

    if conflicts:
        raise MergeConflict(
            "merge would overwrite diverged state — resolve first (§40):\n"
            + "\n".join(conflicts),
            conflicts=conflicts,
        )

    removed: set[str] = set()
    for op in operations:
        if isinstance(op, Delete):
            target._artifacts.pop(op.artifact_id, None)
            removed.add(op.artifact_id)
    for artifact_id, data in pending.items():
        existing = target._artifacts.get(artifact_id)
        if existing is None:
            target._artifacts[artifact_id] = Artifact(data=data, id=artifact_id)
        else:
            existing.update(data)

    for rel in other.relations():
        if rel.source_id in removed or rel.target_id in removed:
            continue
        if (rel.source_id, rel.relation, rel.target_id) not in target._relations:
            target.link(rel.source_id, rel.relation, rel.target_id)
            operations.append(Link(rel.source_id, rel.relation, rel.target_id))

    if operations:
        target.log_commit(
            Commit(author="merge", message=message, operations=operations)
        )
        # merge mutates `_artifacts` directly (create/update/delete above),
        # bypassing the incremental hooks in `create()`/`update()`/
        # `delete()` — resync `_stale` from scratch rather than risk it
        # drifting from the post-merge state.
        target._recompute_stale()
        target._reindex_by_type()


class BranchStore:
    """Persists named branches (`branch:<session>:<name>`) over a KV backend."""

    def __init__(self, backend: KVBackend):
        self.backend = backend

    @staticmethod
    def _key(session_id: str, name: str) -> str:
        return f"branch:{session_id}:{name}"

    async def save_branch(
        self, context: Context, *, session_id: str, name: str
    ) -> None:
        """Saves a branch (including its fork base snapshot, §40)."""
        await context.to_kv(self.backend, self._key(session_id, name))

    async def load_branch(self, session_id: str, name: str) -> Context | None:
        """Loads a branch, or None if it does not exist."""
        return await Context.from_kv(self.backend, self._key(session_id, name))

    async def list_branches(self, session_id: str) -> list[str]:
        prefix = f"branch:{session_id}:"
        names: list[str] = []
        all_keys = await self.backend.keys()
        for key in all_keys:
            if key.startswith(prefix):
                names.append(key[len(prefix) :])
        return sorted(names)

    async def delete_branch(self, session_id: str, name: str) -> None:
        await self.backend.delete(self._key(session_id, name))


__all__ = [
    "BranchStore",
    "MergeConflict",
    "clone_context",
    "fork_context",
    "merge_context_from",
    "merge_contexts",
]
