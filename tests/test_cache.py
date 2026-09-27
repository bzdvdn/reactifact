"""`reactifact.cache` — exact LLM completion cache: hits skip the provider and
are not charged to the token/cost budget."""

from __future__ import annotations

import asyncio

import pytest
from reactifact.budget import BudgetLLM, BudgetTracker
from reactifact.cache import (
    CachingLLM,
    InMemoryCache,
    KVCache,
    cache_key,
    is_cache_hit,
)
from reactifact.checkpoints import InMemoryKVBackend
from reactifact.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    Message,
)


class CountingLLM(LLMProvider):
    def __init__(self, usage: dict | None = None) -> None:
        self.model = "test-model"
        self.usage = usage or {"prompt_tokens": 5, "completion_tokens": 3}
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(text=f"reply {self.calls}", usage=dict(self.usage))

    async def stream(self, request: LLMRequest):
        self.calls += 1
        yield LLMResponseChunk(text=f"chunk {self.calls}")


class FlakyLLM(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient")
        return LLMResponse(text="recovered")

    async def stream(self, request: LLMRequest):
        yield LLMResponseChunk(text="")


def _req(text: str = "hi", **kwargs) -> LLMRequest:
    return LLMRequest(messages=[Message.user(text)], **kwargs)


def test_second_identical_call_is_served_from_cache():
    async def go():
        inner = CountingLLM()
        llm = CachingLLM(inner)
        request = _req()
        first = await llm.complete(request)
        second = await llm.complete(request)
        return inner, first, second

    inner, first, second = asyncio.run(go())

    assert second.text == first.text
    assert inner.calls == 1
    assert not is_cache_hit(first)
    assert is_cache_hit(second)
    assert second.usage == {"prompt_tokens": 5, "completion_tokens": 3}


def test_a_different_request_is_a_miss():
    async def go():
        inner = CountingLLM()
        llm = CachingLLM(inner)
        await llm.complete(_req(temperature=0.0))
        await llm.complete(_req(temperature=1.0))
        return inner

    assert asyncio.run(go()).calls == 2


def test_a_model_override_is_part_of_the_key():
    async def go():
        inner = CountingLLM()
        llm = CachingLLM(inner)
        await llm.complete(_req())
        await llm.complete(_req(extra={"model": "other-model"}))
        return inner

    assert asyncio.run(go()).calls == 2


def test_errors_are_not_cached():
    async def go():
        inner = FlakyLLM()
        llm = CachingLLM(inner)
        with pytest.raises(RuntimeError, match="transient"):
            await llm.complete(_req())
        response = await llm.complete(_req())
        return inner, response

    inner, response = asyncio.run(go())
    assert response.text == "recovered"
    assert inner.calls == 2


def test_kv_cache_persists_across_instances():
    async def go():
        cache = KVCache(InMemoryKVBackend())
        await CachingLLM(CountingLLM(), cache=cache).complete(_req())
        second = CountingLLM()
        response = await CachingLLM(second, cache=cache).complete(_req())
        return second, response

    second, response = asyncio.run(go())
    assert second.calls == 0  # served from the shared cache
    assert is_cache_hit(response)


def test_stream_passes_through_uncached():
    async def go():
        inner = CountingLLM()
        llm = CachingLLM(inner)
        for _ in range(2):
            async for _chunk in llm.stream(_req()):
                pass
        return inner

    assert asyncio.run(go()).calls == 2


def test_cache_hit_is_not_charged_to_the_budget():
    async def go():
        inner = CountingLLM(usage={"prompt_tokens": 5, "completion_tokens": 3})
        tracker = BudgetTracker()
        llm = BudgetLLM(CachingLLM(inner), tracker)
        await llm.complete(_req())
        await llm.complete(_req())
        return inner, tracker

    inner, tracker = asyncio.run(go())
    assert inner.calls == 1
    assert tracker.total_tokens == 8  # only the miss was charged


def test_in_memory_cache_set_get_and_clear():
    async def go():
        cache = InMemoryCache()
        await cache.set("k", {"text": "v"})
        present = await cache.get("k")
        missing = await cache.get("missing")
        size = len(cache)
        cache.clear()
        return present, missing, size, len(cache)

    present, missing, size, after = asyncio.run(go())
    assert present == {"text": "v"}
    assert missing is None
    assert size == 1 and after == 0


def test_cache_key_is_stable_and_request_sensitive():
    assert cache_key("m", _req()) == cache_key("m", _req())
    assert cache_key("m", _req()) != cache_key("m", _req("different"))
