"""Quota — cross-turn, per-principal usage limits (§57).

A `Budget` bounds a *single turn*; a quota bounds a principal (a user, a
tenant) *across turns*: total tokens, cost or calls within a rolling window.
`RuntimeResources(quota=QuotaTracker(...))` turns it on; the runtime checks it
at the start of each turn (setting `RunOutcome.QUOTA_EXCEEDED`) and counts LLM
usage into it for the whole run.

    from reactifact.quota import Quota, QuotaTracker

    resources = RuntimeResources(
        principal=Principal("acme", capabilities=("tenant",)),
        quota=QuotaTracker(Quota(max_tokens=1_000_000, window_seconds=86_400)),
    )

The default store is in-process (like LangChain's `InMemoryRateLimiter`): it
does not coordinate across workers. For a multi-process deployment, pass a
`store=` implementation backed by shared state (Redis, a database) — the
`QuotaStore` protocol is deliberately tiny.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .cache import is_cache_hit
from .pricing import Pricer, cost_of
from .providers.contracts import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
)


@dataclass(frozen=True)
class Quota:
    """Limits for one key over a window (`window_seconds=None` = lifetime)."""

    max_tokens: int | None = None
    max_cost: float | None = None
    max_calls: int | None = None
    window_seconds: float | None = None


@dataclass
class Usage:
    """Consumption recorded for one key in the current window."""

    tokens: int = 0
    cost: float = 0.0
    calls: int = 0


class QuotaExceeded(Exception):
    """Raised/surfaced when a key has spent its quota for the window."""

    def __init__(self, key: str, limit: str, usage: Usage) -> None:
        self.key = key
        self.limit = limit
        self.usage = usage
        super().__init__(f"quota {limit} exceeded for {key!r}")


@dataclass
class _Window:
    started: float
    usage: Usage = field(default_factory=Usage)


class QuotaStore(Protocol):
    """Minimal persistence for quota windows (in-memory by default)."""

    def get(self, key: str) -> _Window | None: ...
    def set(self, key: str, window: _Window) -> None: ...


class InMemoryQuotaStore:
    """A process-local store — fine for one worker, not for a cluster."""

    def __init__(self) -> None:
        self._windows: dict[str, _Window] = {}

    def get(self, key: str) -> _Window | None:
        return self._windows.get(key)

    def set(self, key: str, window: _Window) -> None:
        self._windows[key] = window


class QuotaTracker:
    """Checks and records usage against a `Quota`, keyed by principal."""

    def __init__(
        self,
        quota: Quota,
        *,
        store: QuotaStore | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.quota = quota
        self.store = store if store is not None else InMemoryQuotaStore()
        self._clock = clock

    def _window(self, key: str, *, now: float) -> _Window:
        window = self.store.get(key)
        expired = (
            window is not None
            and self.quota.window_seconds is not None
            and now - window.started >= self.quota.window_seconds
        )
        if window is None or expired:
            window = _Window(started=now)
            self.store.set(key, window)
        return window

    def usage(self, key: str) -> Usage:
        return self._window(key, now=self._clock()).usage

    def exceeded(self, key: str) -> QuotaExceeded | None:
        """The exceeded limit, or `None` while the key is within quota."""
        usage = self.usage(key)
        quota = self.quota
        if quota.max_tokens is not None and usage.tokens >= quota.max_tokens:
            return QuotaExceeded(key, "max_tokens", usage)
        if quota.max_cost is not None and usage.cost >= quota.max_cost:
            return QuotaExceeded(key, "max_cost", usage)
        if quota.max_calls is not None and usage.calls >= quota.max_calls:
            return QuotaExceeded(key, "max_calls", usage)
        return None

    def record(
        self, key: str, *, tokens: int = 0, cost: float = 0.0, calls: int = 0
    ) -> None:
        window = self._window(key, now=self._clock())
        window.usage.tokens += tokens
        window.usage.cost += cost
        window.usage.calls += calls

    def reset(self, key: str | None = None) -> None:
        """Clears one key's window (or all of them)."""
        if key is None:
            self.store = type(self.store)()
            return
        self.store.set(key, _Window(started=self._clock()))


class QuotaLLM(LLMProvider):
    """Counts token/cost/call usage into a `QuotaTracker` for one key.

    A transparent proxy like `BudgetLLM`; the runtime installs it for the turn
    when `RuntimeResources.quota` is set. Cache hits are not charged.
    """

    def __init__(
        self,
        inner: LLMProvider,
        tracker: QuotaTracker,
        *,
        key: str,
        pricer: Pricer | None = None,
    ) -> None:
        self._inner = inner
        self._tracker = tracker
        self._key = key
        self._pricer = pricer

    def _account(self, usage: dict[str, Any] | None, *, calls: int) -> None:
        prompt = completion = 0
        if usage:
            prompt = int(usage.get("prompt_tokens") or usage.get("prompt") or 0)
            completion = int(
                usage.get("completion_tokens") or usage.get("completion") or 0
            )
        cost = 0.0
        if self._pricer is not None:
            model = str(getattr(self._inner, "model", "") or "")
            cost = cost_of(self._pricer, model, prompt, completion)
        self._tracker.record(
            self._key, tokens=prompt + completion, cost=cost, calls=calls
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await self._inner.complete(request)
        if is_cache_hit(response):
            return response
        self._account(response.usage, calls=1)
        return response

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        first = True
        async for chunk in self._inner.stream(request):
            if first:
                self._account(None, calls=1)
                first = False
            if chunk.usage:
                self._account(chunk.usage, calls=0)
            yield chunk

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


__all__ = [
    "InMemoryQuotaStore",
    "Quota",
    "QuotaExceeded",
    "QuotaLLM",
    "QuotaStore",
    "QuotaTracker",
    "Usage",
]
