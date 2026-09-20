"""Loop-aware `httpx.AsyncClient` ownership for providers and sinks.

`httpx.AsyncClient` binds its connection pool (and internal locks) to the event
loop it was created on. A provider/sink instance that caches the client forever
and is then reused on a *different* loop — pytest gives each test its own loop,
a CLI calls `asyncio.run(...)` more than once, uvicorn reloads — raises
`RuntimeError: Event loop is closed` (or "bound to a different event loop") on
the second use.

`LoopBoundClient` keeps the client **lazy** (created on first use, as before)
but **rebinds it to the current running loop**: when the loop changes, a fresh
client is created. The previous client's loop is already gone, so it is dropped
rather than awaited (its pool died with the loop). An injected `client=` (tests,
a custom transport) is returned as-is and never recreated.

Typical use inside a provider:

    self._http = LoopBoundClient(
        lambda: httpx.AsyncClient(timeout=self._timeout, headers=self._headers)
    )

    async def complete(self, ...):
        response = await self._http.get().post(url, json=payload)

    async def aclose(self) -> None:
        await self._http.aclose()
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

import httpx


class LoopBoundClient:
    """Owns an `httpx.AsyncClient` bound to the current event loop."""

    def __init__(
        self,
        factory: Callable[[], httpx.AsyncClient],
        *,
        client: Any | None = None,
    ):
        self._factory = factory
        self._fixed = client
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def get(self) -> httpx.AsyncClient:
        """The client for the running loop (created/recreated as needed)."""
        if self._fixed is not None:
            return cast("httpx.AsyncClient", self._fixed)
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            self._client = self._factory()
            self._loop = loop
        return self._client

    async def aclose(self) -> None:
        """Closes the current client, if it belongs to the running loop.

        A client whose owner loop is closed (or is a different, still-running
        loop) cannot be awaited here; it is dropped — nothing can safely close
        it from this loop. The injected `client=`, if any, is left to its owner.
        """
        client, loop = self._client, self._loop
        self._client = None
        self._loop = None
        if client is None:
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            return
        if loop is not None and loop is not current:
            return
        await client.aclose()

    def raw(self) -> Any | None:
        """The injected client if there is one, else the current one (or None)."""
        return self._fixed if self._fixed is not None else self._client


__all__ = ["LoopBoundClient"]
