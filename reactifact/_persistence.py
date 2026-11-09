"""Internal: append-only persistence helpers shared by session/branch stores.

Not public API — `SessionStore`/`BranchStore` use these when the backend
implements `AppendBackend` (SQLite/Postgres). A save writes a full snapshot on
the first save, after a compaction, or after a rollback; otherwise it appends
only the new commits plus the current pending-event queue.
"""

from __future__ import annotations

from typing import Any, cast

from .artifacts import Artifact
from .checkpoints import AppendBackend
from .commit import Commit
from .context import Context
from .events import Event
from .patches import Relation
from .relations import RelationGraph

#: Default number of appended saves before a snapshot rewrite. Bounds load
#: replay (and log size) for long-lived sessions without an explicit
#: `context.compact()`.
DEFAULT_COMPACT_EVERY = 100


async def save_append(
    backend: AppendBackend,
    key: str,
    context: Context,
    *,
    compact_every: int | None = DEFAULT_COMPACT_EVERY,
) -> None:
    persisted, persisted_compacted = context.persisted_state(key)
    needs_snapshot = (
        persisted < 0
        or persisted > context.version
        or context.compacted_at > persisted_compacted
        or await backend.get(key) is None
    )
    if (
        not needs_snapshot
        and compact_every is not None
        and await backend.entry_count(key) + 1 >= compact_every
    ):
        needs_snapshot = True
    if needs_snapshot:
        await backend.set(key, cast(dict[str, Any], context.to_dict()))
        await backend.clear_entries(key)
        context.mark_persisted(key=key)
        return
    new_commits = [
        commit.to_dict()
        for commit in context.commit_log()
        if (commit.context_version or 0) > persisted
    ]
    working_tree = _working_tree(context)
    await backend.append(
        key,
        {
            "commits": new_commits,
            "events": [event.to_dict() for event in context.pending_events()],
            # `Context.create()` adds the turn's user artifact to the working
            # tree, not to a commit. Without this snapshot append-only stores
            # lose every user message after the first save (only assistant
            # FinalResponse commits survive a reload). Each entry carries the
            # complete current working tree; the last entry wins on load.
            "working_tree": working_tree,
        },
    )
    context.mark_persisted(key=key)


def _working_tree(context: Context) -> dict[str, Any]:
    """Artifacts/relations that live outside the commit log (the working tree)."""
    committed = context._log.replay_state(context.version)
    artifacts = {
        aid: artifact.to_dict()
        for aid, artifact in context._artifacts.items()
        if aid not in committed or committed[aid] != artifact.data
    }
    committed_relations = context._log.replay_relations(context.version)
    relations = [
        relation.to_dict()
        for key, relation in context._relations.items()
        if key not in committed_relations or committed_relations[key] != relation
    ]
    return {
        "artifacts": artifacts,
        "relations": relations,
        # Working-tree artifacts are merged after commit replay; their IDs in
        # the live insertion order let load restore the exact conversation
        # order instead of appending every user message after assistant turns.
        "order": [artifact.id for artifact in context.list_artifacts()],
    }


def _apply_working_tree(context: Context, payload: dict[str, Any]) -> None:
    """Make the latest appended working tree authoritative over the snapshot."""
    committed = context._log.replay_state(context.version)
    artifacts = payload.get("artifacts") or {}
    for artifact_id in list(context._artifacts):
        if artifact_id not in committed and artifact_id not in artifacts:
            context._artifacts.pop(artifact_id, None)
            context._store.delete(artifact_id)
    for artifact_id, artifact_dict in artifacts.items():
        artifact = Artifact.from_dict(artifact_dict)
        if artifact._store is not None:
            context._store.extend(artifact._store)
        current = context._store.current_artifact(artifact_id)
        if current is not None:
            context._artifacts[artifact_id] = current

    committed_relations = context._log.replay_relations(context.version)
    wanted: dict[Any, Relation] = {}
    for item in payload.get("relations") or []:
        relation = Relation.from_dict(item)
        wanted[(relation.source_id, relation.relation, relation.target_id)] = relation
    context._relations = RelationGraph.from_mapping({**committed_relations, **wanted})
    order = payload.get("order")
    if order:
        rank = {artifact_id: index for index, artifact_id in enumerate(order)}
        context._artifacts = dict(
            sorted(
                context._artifacts.items(),
                key=lambda item: rank.get(item[0], len(rank)),
            )
        )
    context.rebuild_indices()


async def load_append(backend: AppendBackend, key: str) -> Context | None:
    snapshot = await backend.get(key)
    if snapshot is None:
        return None
    context = Context.from_dict(snapshot)
    last_working_tree: dict[str, Any] | None = None
    for entry in await backend.entries(key):
        for commit_dict in entry.get("commits", []):
            context.apply_commit(Commit.from_dict(commit_dict), reindex=False)
        if "events" in entry:
            context.replace_events(
                [Event.from_dict(event) for event in entry["events"]]
            )
        if "working_tree" in entry:
            last_working_tree = entry["working_tree"]
    if last_working_tree is not None:
        _apply_working_tree(context, last_working_tree)
    context.rebuild_indices()
    context.mark_persisted(key=key)
    return context
