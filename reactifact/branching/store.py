"""Durable branch persistence (`BranchStore`) over a KV backend (§39-§40)."""

from __future__ import annotations

from .._persistence import DEFAULT_COMPACT_EVERY, load_append, save_append
from ..checkpoints import AppendBackend, KVBackend
from ..context import Context


class BranchStore:
    """Persists named branches (`branch:<session>:<name>`) over a KV backend.

    With an `AppendBackend` (SQLite/Postgres) branches use the same
    snapshot + append-only commit log as sessions, keyed per branch name.
    """

    def __init__(
        self,
        backend: KVBackend,
        *,
        compact_every: int | None = DEFAULT_COMPACT_EVERY,
    ):
        if compact_every is not None and compact_every < 1:
            raise ValueError("compact_every must be >= 1 or None")
        self.backend = backend
        self.compact_every = compact_every

    @staticmethod
    def _key(session_id: str, name: str) -> str:
        return f"branch:{session_id}:{name}"

    async def save_branch(
        self, context: Context, *, session_id: str, name: str
    ) -> None:
        """Saves a branch (including its fork base snapshot, §40)."""
        key = self._key(session_id, name)
        if isinstance(self.backend, AppendBackend):
            await save_append(
                self.backend, key, context, compact_every=self.compact_every
            )
            return
        await context.to_kv(self.backend, key)
        context.mark_persisted(key=key)

    async def load_branch(self, session_id: str, name: str) -> Context | None:
        """Loads a branch, or None if it does not exist."""
        key = self._key(session_id, name)
        if isinstance(self.backend, AppendBackend):
            return await load_append(self.backend, key)
        return await Context.from_kv(self.backend, key)

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


__all__ = ["BranchStore"]
