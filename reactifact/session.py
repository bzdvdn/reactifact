from __future__ import annotations

import asyncio
import time
from typing import Any
from weakref import WeakKeyDictionary

from ._persistence import DEFAULT_COMPACT_EVERY, load_append, save_append
from .checkpoints import AppendBackend, KVBackend
from .commit import ContextCheckpoint
from .context import Context
from .resources import RuntimeResources


class Session:
    """Named session: an isolated Context bound to a key-value store.

    With an `AppendBackend` (SQLite/Postgres) the store keeps a snapshot plus
    an append-only commit log, so a turn writes only its new commits instead
    of the whole Context. Other backends keep the full-blob behavior.
    """

    def __init__(
        self,
        session_id: str,
        context: Context,
        store: SessionStore,
        loaded: bool,
    ):
        self.session_id = session_id
        self.context = context
        self._store = store
        self.loaded = loaded

    async def save(self) -> None:
        await self._store.save_session(self.session_id, self.context)

    async def delete(self) -> None:
        await self._store.delete_session(self.session_id)

    def checkpoints(self) -> list[ContextCheckpoint]:
        """This session's history as lightweight checkpoints."""
        return self.context.checkpoints()

    async def fork(
        self,
        new_id: str,
        *,
        at: int | str | ContextCheckpoint | None = None,
        overwrite: bool = False,
    ) -> Session:
        """Starts a new session from this one (optionally at a checkpoint)."""
        return await self._store.fork(
            self.session_id,
            new_id=new_id,
            at=at,
            resources=self.context.resources,
            overwrite=overwrite,
        )


class SessionStore:
    """Session store on top of KVBackend (session_id → Context).

    When the backend implements `AppendBackend` (the built-in SQLite and
    Postgres stores), a save writes a snapshot only on the first save, after a
    compaction, or after a rollback; subsequent saves append just the new
    commits plus the current pending-event queue. This keeps a turn's write
    cost proportional to what changed instead of O(whole context).

    `compact_every=N` (default 100) bounds the append log: roughly every N
    saves the snapshot is rewritten and the log cleared. `None` keeps the log
    until an explicit `context.compact()` or a rollback. Ignored by backends
    that are not `AppendBackend`.
    """

    def __init__(
        self,
        backend: KVBackend,
        *,
        compact_every: int | None = DEFAULT_COMPACT_EVERY,
        invocation_stale_after: float | None = 600.0,
    ):
        if compact_every is not None and compact_every < 1:
            raise ValueError("compact_every must be >= 1 or None")
        if invocation_stale_after is not None and invocation_stale_after < 0:
            raise ValueError("invocation_stale_after must be >= 0 or None")
        self.backend = backend
        self.compact_every = compact_every
        #: A `running` invocation older than this (seconds) may be claimed
        #: again, so a crashed worker does not block retries forever. `None`
        #: disables stale reclaim.
        self.invocation_stale_after = invocation_stale_after
        # One in-process guard per event loop: an `asyncio.Lock` binds to the
        # loop where it first waits, so a store reused across `asyncio.run`
        # calls must not share one lock. Backends with a native
        # `set_if_absent` make the claim atomic across processes; the guard
        # only serializes the fallback path and stale re-claims.
        self._invocation_guards: WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = WeakKeyDictionary()

    async def save_session(self, session_id: str, context: Context) -> None:
        if isinstance(self.backend, AppendBackend):
            await save_append(
                self.backend,
                session_id,
                context,
                compact_every=self.compact_every,
            )
            return
        await context.to_kv(self.backend, session_id)
        context.mark_persisted(key=session_id)

    async def load_session(
        self,
        session_id: str,
        resources: RuntimeResources | None = None,
    ) -> Context | None:
        if isinstance(self.backend, AppendBackend):
            context = await load_append(self.backend, session_id)
        else:
            context = await Context.from_kv(self.backend, session_id)
        if context is None:
            return None
        context.resources = resources or RuntimeResources()
        return context

    async def has_session(self, session_id: str) -> bool:
        return await self.backend.get(session_id) is not None

    async def list_sessions(self) -> list[str]:
        # Branch keys and invocation records are not sessions.
        keys = await self.backend.keys()
        return [
            key
            for key in keys
            if not key.startswith("branch:") and not key.startswith("invocation:")
        ]

    async def get_invocation(self, key: str) -> dict[str, Any] | None:
        """The idempotency record for `key`, if any."""
        return await self.backend.get(f"invocation:{key}")

    async def put_invocation(self, key: str, record: dict[str, Any]) -> None:
        """Create/update an idempotency record (must be JSON-serializable)."""
        await self.backend.set(f"invocation:{key}", record)

    async def delete_invocation(self, key: str) -> None:
        await self.backend.delete(f"invocation:{key}")

    def _invocation_guard(self) -> asyncio.Lock:
        """The claim lock for the current event loop (created on first use)."""
        loop = asyncio.get_running_loop()
        guard = self._invocation_guards.get(loop)
        if guard is None:
            guard = asyncio.Lock()
            self._invocation_guards[loop] = guard
        return guard

    def _invocation_is_stale(self, record: dict[str, Any]) -> bool:
        if self.invocation_stale_after is None:
            return False
        if record.get("status") != "running":
            return False
        started_at = record.get("started_at")
        if not isinstance(started_at, (int, float)):
            # Records written before the lease was stored cannot be trusted
            # to still be running.
            return True
        return time.time() - started_at >= self.invocation_stale_after

    async def claim_invocation(
        self,
        key: str,
        record: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None]:
        """Claim `key` for this attempt, atomically where the backend can.

        A backend with a native `set_if_absent` (memory/SQLite/Postgres/file)
        makes the claim atomic across processes. Otherwise the per-loop guard
        serializes read-then-set inside this process only. A `running` record
        older than `invocation_stale_after` is re-claimable so a crashed
        worker does not block retries forever.

        Returns `(True, None)` when the caller now owns the invocation, or
        `(False, existing_record)` when another attempt already recorded it.
        """
        claimed_record = {**record, "started_at": time.time()}
        async with self._invocation_guard():
            if await self.backend.set_if_absent(f"invocation:{key}", claimed_record):
                return True, None
            existing = await self.get_invocation(key)
            if existing is None:
                # A backend's fallback lost a delete race; retry once.
                await self.put_invocation(key, claimed_record)
                return True, None
            if self._invocation_is_stale(existing):
                await self.put_invocation(key, claimed_record)
                return True, None
            return False, existing

    async def delete_session(self, session_id: str) -> None:
        await self.backend.delete(session_id)

    async def checkpoints(
        self,
        session_id: str,
        resources: RuntimeResources | None = None,
    ) -> list[ContextCheckpoint]:
        """A session's commit history as lightweight checkpoints."""
        context = await self.load_session(session_id, resources)
        if context is None:
            raise KeyError(f"session {session_id!r} not found")
        return context.checkpoints()

    async def fork(
        self,
        session_id: str,
        *,
        new_id: str,
        at: int | str | ContextCheckpoint | None = None,
        resources: RuntimeResources | None = None,
        overwrite: bool = False,
    ) -> Session:
        """Creates a new session from `session_id`, optionally at a checkpoint.

        With `at=`, the new session starts from that committed state as a
        detached snapshot (no pending events/step cursors), which is the
        time-travel primitive. Without `at=`, the current state (including a
        pending trigger queue) is copied. The source session is never mutated.
        """
        if not new_id:
            raise ValueError("new_id must be non-empty")
        if not overwrite and await self.has_session(new_id):
            raise ValueError(f"session {new_id!r} already exists")
        context = await self.load_session(session_id, resources)
        if context is None:
            raise KeyError(f"session {session_id!r} not found")
        if at is not None:
            context = context.at(at)
        await self.save_session(new_id, context)
        return Session(new_id, context, self, loaded=True)

    async def open(
        self,
        session_id: str,
        resources: RuntimeResources | None = None,
    ) -> Session:
        """Opens a session: loads an existing one or creates an empty one."""
        context = await self.load_session(session_id, resources)
        loaded = context is not None
        if context is None:
            context = Context(resources=resources or RuntimeResources())
        return Session(session_id, context, self, loaded=loaded)
