"""reactifact.cache — an exact (keyed) cache for LLM completions.

Every `complete()` is keyed by the whole request (effective model, messages,
temperature, max_tokens, stop, response_format, extra) and, on a hit, is
answered from the store without calling the provider — no tokens, no latency:

    resources.llm = CachingLLM(provider)                       # process-local
    resources.llm = CachingLLM(                            # survives restarts
        provider, cache=KVCache(SQLiteKVBackend("llm-cache.db"))
    )

Deliberately **not semantic**: an approximate match would answer a different
question with a cached one, which is at odds with the framework's determinism
(§59). Only `complete()` is cached — `stream()` passes through, since a stream
is a sequence the caller consumes, not a single value. Errors are never cached.

A hit carries `raw={"reactifact_cache": "hit"}` (its `usage` is preserved for
observability) and `BudgetLLM` skips cost/token accounting for it, because a
cached call spends nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

from .checkpoints import KVBackend
from .providers import LLMProvider, LLMRequest, LLMResponse, LLMResponseChunk

#: Marks a cache-hit response in `LLMResponse.raw` (read by `BudgetLLM`).
CACHE_HIT = "hit"
_CACHE_MARKER = "reactifact_cache"


def is_cache_hit(response: LLMResponse) -> bool:
    """Whether `response` was served from the cache (never hit a provider)."""
    raw = response.raw
    return isinstance(raw, dict) and raw.get(_CACHE_MARKER) == CACHE_HIT


def cache_key(model: str, request: LLMRequest) -> str:
    """Stable key over everything that can change the completion."""
    payload = {
        "model": model,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stop": request.stop,
        "response_format": request.response_format,
        "extra": request.extra,
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResponseCache(Protocol):
    """Storage for cached completions (async — `CachingLLM.complete` is async)."""

    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def set(self, key: str, value: dict[str, Any]) -> None: ...


class InMemoryCache:
    """Process-local dict cache — no persistence, the zero-config default."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        value = self._data.get(key)
        return dict(value) if value is not None else None

    async def set(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = dict(value)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class KVCache:
    """A `ResponseCache` over any `reactifact.checkpoints.KVBackend`.

    Reuses the session/checkpoint backends (`FileKVBackend`, `SQLiteKVBackend`,
    `PostgreSQLKVBackend`), so the cache can survive a process restart.
    """

    def __init__(self, backend: KVBackend, *, prefix: str = "llm-cache:"):
        self._backend = backend
        self._prefix = prefix

    async def get(self, key: str) -> dict[str, Any] | None:
        return await self._backend.get(self._prefix + key)

    async def set(self, key: str, value: dict[str, Any]) -> None:
        await self._backend.set(self._prefix + key, value)


def _to_stored(response: LLMResponse) -> dict[str, Any]:
    """The persisted shape: provider `raw` is dropped (not always JSON-safe)."""
    return {
        "text": response.text,
        "finish_reason": response.finish_reason,
        "usage": dict(response.usage),
    }


def _from_stored(stored: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        text=stored.get("text", ""),
        raw={_CACHE_MARKER: CACHE_HIT},
        finish_reason=stored.get("finish_reason"),
        usage=dict(stored.get("usage") or {}),
    )


class CachingLLM(LLMProvider):
    """A provider wrapper: answer a repeated `complete()` from the cache."""

    def __init__(
        self,
        inner: LLMProvider,
        *,
        cache: ResponseCache | None = None,
    ):
        self._inner = inner
        self._cache: ResponseCache = cache if cache is not None else InMemoryCache()

    def _model(self, request: LLMRequest) -> str:
        override = request.extra.get("model")
        return str(override or getattr(self._inner, "model", "") or "")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        key = cache_key(self._model(request), request)
        stored = await self._cache.get(key)
        if stored is not None:
            return _from_stored(stored)
        response = await self._inner.complete(request)
        await self._cache.set(key, _to_stored(response))
        return response

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        # Not cached: a stream is consumed incrementally, not replayed as one value.
        return self._inner.stream(request)

    async def aclose(self) -> None:
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            await aclose()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


__all__ = [
    "CACHE_HIT",
    "CachingLLM",
    "InMemoryCache",
    "KVCache",
    "ResponseCache",
    "cache_key",
    "is_cache_hit",
]
