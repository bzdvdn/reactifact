"""Internal: append-only persistence helpers shared by session/branch stores.

Not public API — `SessionStore`/`BranchStore` use these when the backend
implements `AppendBackend` (SQLite/Postgres). A save writes a full snapshot on
the first save, after a compaction, or after a rollback; otherwise it appends
only the new commits plus the current pending-event queue.
"""

from __future__ import annotations

from typing import Any, cast

from .checkpoints import AppendBackend
from .commit import Commit
from .context import Context
from .events import Event

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
    await backend.append(
        key,
        {
            "commits": new_commits,
            "events": [event.to_dict() for event in context.pending_events()],
        },
    )
    context.mark_persisted(key=key)


async def load_append(backend: AppendBackend, key: str) -> Context | None:
    snapshot = await backend.get(key)
    if snapshot is None:
        return None
    context = Context.from_dict(snapshot)
    for entry in await backend.entries(key):
        for commit_dict in entry.get("commits", []):
            context.apply_commit(Commit.from_dict(commit_dict), reindex=False)
        if "events" in entry:
            context.replace_events(
                [Event.from_dict(event) for event in entry["events"]]
            )
    context.rebuild_indices()
    context.mark_persisted(key=key)
    return context
