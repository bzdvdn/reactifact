"""The fake external world.

A real dispatcher would call an email or webhook API here — and pass the
`idempotency_key` through so *that* system can dedupe too, because resume is
at-least-once (a crash between the send and the `dispatched` commit re-runs it).
The demo keeps the "external system" in memory so the effect is visible as a
count: no delivery should ever appear twice.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from reactifact import Artifact, Context, PendingAction

Dispatcher = Callable[[Context, Artifact[PendingAction]], Awaitable[None]]


class SentLog:
    """The fake inbox: one entry per delivered `idempotency_key`."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def __len__(self) -> int:
        return len(self.sent)

    def keys(self) -> list[str]:
        return list(self.sent)


def make_dispatcher(
    log: SentLog, *, fail_first: bool = False
) -> tuple[Dispatcher, dict[str, Any]]:
    """A dispatcher that dedupes on the key, optionally failing the first call.

    Returns `(dispatcher, state)` so the caller can flip `state["fail_next"]`
    or inspect it — `fail_first=True` models a transient outage for the
    retry demo in `main.failure_retry`.
    """
    state: dict[str, Any] = {"fail_next": fail_first}

    async def dispatch(context: Context, action: Artifact[PendingAction]) -> None:
        if state["fail_next"]:
            state["fail_next"] = False
            raise RuntimeError("smtp temporarily unavailable")
        key = action.data.idempotency_key
        if key in log.sent:  # the external system's own idempotency guard
            return
        log.sent.append(key)

    return dispatch, state


def make_flaky(inner: Dispatcher, *, fail_times: int) -> Dispatcher:
    """A dispatcher that fails the first `fail_times` calls (a transient outage)."""
    state = {"remaining": fail_times}

    async def dispatch(context: Context, action: Artifact[PendingAction]) -> None:
        if state["remaining"] > 0:
            state["remaining"] -= 1
            raise RuntimeError("smtp temporarily unavailable")
        await inner(context, action)

    return dispatch


def with_retries(
    inner: Dispatcher,
    *,
    attempts: int = 3,
    delay: float = 0.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Dispatcher:
    """An app-owned retry wrapper — the outbox leaves backoff to the application.

    Retrying *inside* the dispatcher keeps the runtime seeing a single outcome:
    the action is dispatched once (or marked `failed` when every attempt is
    exhausted), and `PendingAction.attempts` never accumulates for transient
    blips the wrapper absorbed.
    """

    async def dispatch(context: Context, action: Artifact[PendingAction]) -> None:
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await inner(context, action)
                return
            except Exception as exc:  # noqa: BLE001 — retried, then re-raised
                last = exc
                if attempt < attempts:
                    await sleep(delay)
        assert last is not None
        raise last

    return dispatch
