"""The fake external world: an idempotent in-memory dispatcher."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from reactifact import Artifact, Context, PendingAction

Dispatcher = Callable[[Context, Artifact[PendingAction]], Awaitable[None]]


class SentLog:
    """The fake inbox: one entry per delivered idempotency key."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def __len__(self) -> int:
        return len(self.sent)

    def keys(self) -> list[str]:
        return list(self.sent)


def make_dispatcher(
    log: SentLog, *, fail_first: bool = False
) -> tuple[Dispatcher, dict[str, Any]]:
    state: dict[str, Any] = {"fail_next": fail_first}

    async def dispatch(context: Context, action: Artifact[PendingAction]) -> None:
        if state["fail_next"]:
            state["fail_next"] = False
            raise RuntimeError("smtp temporarily unavailable")
        key = action.data.idempotency_key
        if key in log.sent:
            return
        log.sent.append(key)

    return dispatch, state


def make_flaky(inner: Dispatcher, *, fail_times: int) -> Dispatcher:
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
