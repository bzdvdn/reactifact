"""Outbox drain: perform committed `PendingAction`s once, after commit (§42)."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable

from ..artifacts import Artifact
from ..context import Context
from ..interrupt import PendingAction
from ..logging import get_logger

logger = get_logger(__name__)

#: Performs one committed `PendingAction`. The app does the real I/O and must
#: be idempotent on `idempotency_key` (§42).
Dispatcher = Callable[[Context, Artifact[PendingAction]], Awaitable[None]]
#: Called when the dispatcher raises (the action is already marked `failed`).
DispatchErrorHandler = Callable[[Artifact[PendingAction], BaseException], None]


class Outbox:
    """Dispatches committed-but-undispatched outbox intents."""

    def __init__(
        self,
        context: Context,
        dispatcher: Dispatcher | None,
        on_dispatch_error: DispatchErrorHandler | None,
    ) -> None:
        self.context = context
        self.dispatcher = dispatcher
        self.on_dispatch_error = on_dispatch_error
        self._warned = False

    def has_pending_actions(self) -> bool:
        return self.dispatcher is not None and bool(self.context.pending_actions())

    def pending_count(self) -> int:
        return len(self.context.pending_actions())

    def warn_if_unconfigured(self) -> None:
        if self._warned or self.dispatcher is not None:
            return
        pending = self.pending_count()
        if not pending:
            return
        self._warned = True
        print(
            f"{pending} PendingAction(s) are committed but no dispatcher= is "
            "configured on this Runtime, so the outbound side effects will not "
            "run. Pass Runtime(dispatcher=...) (or call flush_pending_actions() "
            "with one).",
            file=sys.stderr,
        )

    async def drain_actions(self) -> None:
        """Perform every committed action once, in creation order.

        Success records `dispatched`; failure records `failed` and re-raises
        (fail-loud, §69). Stable-id dedupe is what stops two produces in one
        generation from double-sending.
        """
        if self.dispatcher is None:
            return
        for action in list(self.context.pending_actions()):
            try:
                await self.dispatcher(self.context, action)
            except Exception as exc:
                self.context.mark_failed(action.id, error=repr(exc))
                logger.warning(
                    "action dispatch failed",
                    extra={
                        "action_kind": action.data.kind,
                        "action_key": action.data.idempotency_key,
                        "error": repr(exc),
                    },
                )
                if self.on_dispatch_error is not None:
                    self.on_dispatch_error(action, exc)
                raise
            self.context.mark_dispatched(action.id)
            logger.info(
                "action dispatched",
                extra={
                    "action_kind": action.data.kind,
                    "action_key": action.data.idempotency_key,
                },
            )


__all__ = ["DispatchErrorHandler", "Dispatcher", "Outbox"]
