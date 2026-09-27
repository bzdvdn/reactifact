"""reactifact.routing — try several LLM providers in order (failover).

`RouterLLM([primary, secondary, ...])` calls the first provider and, when it
fails, falls back to the next — a provider outage (5xx / transport / timeout), a
rate limit, or a missing key while another provider works:

    resources.llm = RouterLLM([openrouter_llm(), groq_llm()])

By default **any** exception triggers a fallback; pass `should_fallback=` to
narrow it (e.g. `retryable_only` skips a 4xx, which is a client problem that
would repeat on every provider). `on_fallback(provider, exc)` is called just
before moving on, for logging/metrics.

Only `complete()` fails over — `stream()` uses the first provider, because a
mid-stream fallback can't replay what the caller already consumed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import httpx

from .providers import LLMProvider, LLMRequest, LLMResponse

#: A provider that raises is discarded; this sees the exception.
FallbackPredicate = Callable[[BaseException], bool]
OnFallback = Callable[[LLMProvider, BaseException], None]


def retryable_only(exc: BaseException) -> bool:
    """`should_fallback` predicate: transport/timeout errors and 429/5xx only.

    A 4xx (bad request, auth) is a client problem that will repeat on every
    provider, so it is not worth failing over.
    """
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


class RouterLLM(LLMProvider):
    """Provider failover in declared order (first = preferred)."""

    def __init__(
        self,
        providers: Iterable[LLMProvider],
        *,
        should_fallback: FallbackPredicate | None = None,
        on_fallback: OnFallback | None = None,
    ):
        self._providers = list(providers)
        if not self._providers:
            raise ValueError("RouterLLM needs at least one provider")
        self._should_fallback: FallbackPredicate = should_fallback or (
            lambda _exc: True
        )
        self._on_fallback = on_fallback

    @property
    def providers(self) -> list[LLMProvider]:
        return list(self._providers)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        last = len(self._providers) - 1
        for index, provider in enumerate(self._providers):
            try:
                return await provider.complete(request)
            except Exception as exc:  # noqa: BLE001 — decide by predicate, then re-raise
                if index == last or not self._should_fallback(exc):
                    raise
                if self._on_fallback is not None:
                    self._on_fallback(provider, exc)
        raise AssertionError("unreachable")  # pragma: no cover

    def stream(self, request: LLMRequest) -> Any:
        # No mid-stream fallback: the caller already consumed the prefix.
        return self._providers[0].stream(request)

    async def aclose(self) -> None:
        for provider in self._providers:
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    def __getattr__(self, name: str) -> Any:
        # `.model` and other provider knobs resolve to the preferred provider.
        return getattr(self._providers[0], name)


__all__ = ["FallbackPredicate", "OnFallback", "RouterLLM", "retryable_only"]
