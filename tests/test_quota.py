"""Quota: cross-turn per-principal usage limits (§57)."""

import asyncio

from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    FakeLLM,
    Produce,
    RunOutcome,
    Runtime,
    RuntimeResources,
    create_agent,
)
from reactifact.authz import Principal
from reactifact.providers.contracts import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    Message,
)
from reactifact.quota import Quota, QuotaLLM, QuotaTracker
from reactifact.structured import structured_llm


class Question(BaseModel):
    text: str


class Sentiment(BaseModel):
    label: str


class Classify(Produce[Sentiment]):
    artifact_type = Sentiment

    async def produce(self, call):
        result = await structured_llm(
            call.context, schema=Sentiment, user=call.trigger.data.text
        )
        if result is not None:
            call.effects.create(result)


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #


def test_tracker_records_and_exceeds():
    tracker = QuotaTracker(Quota(max_calls=1, max_tokens=10))
    assert tracker.exceeded("k") is None
    tracker.record("k", calls=1, tokens=4)
    assert tracker.exceeded("k") is not None
    assert tracker.usage("k").tokens == 4


def test_tracker_window_resets():
    now = [0.0]
    tracker = QuotaTracker(Quota(max_calls=1, window_seconds=10), clock=lambda: now[0])
    tracker.record("k", calls=1)
    assert tracker.exceeded("k") is not None
    now[0] = 11.0
    assert tracker.exceeded("k") is None


def test_tracker_reset():
    tracker = QuotaTracker(Quota(max_calls=1))
    tracker.record("k", calls=1)
    tracker.reset("k")
    assert tracker.exceeded("k") is None


# --------------------------------------------------------------------------- #
# QuotaLLM
# --------------------------------------------------------------------------- #


def test_quota_llm_records_usage():
    tracker = QuotaTracker(Quota(max_tokens=1000))
    llm = QuotaLLM(
        FakeLLM("ok", usage={"prompt_tokens": 10, "completion_tokens": 5}),
        tracker,
        key="alice",
    )
    asyncio.run(llm.complete(LLMRequest(messages=[Message.user("hi")])))
    usage = tracker.usage("alice")
    assert usage.tokens == 15
    assert usage.calls == 1


# --------------------------------------------------------------------------- #
# Runtime integration
# --------------------------------------------------------------------------- #


def _resources(quota: QuotaTracker) -> RuntimeResources:
    return RuntimeResources(
        llm=FakeLLM('{"label": "ok"}'),
        principal=Principal(id="acme"),
        quota=quota,
    )


def _agent():
    return create_agent("classify", consumes=[Consume(Question)], produces=[Classify()])


def test_runtime_counts_then_stops_on_quota():
    tracker = QuotaTracker(Quota(max_calls=1))
    ctx = Context(resources=_resources(tracker))
    ctx.create(Question(text="first"))
    runtime = Runtime(ctx, agents=[_agent()])
    asyncio.run(runtime.arun())

    assert tracker.usage("acme").calls == 1
    assert len(ctx.list_artifacts(Sentiment)) == 1

    ctx.create(Question(text="second"))
    asyncio.run(runtime.arun())
    assert runtime.outcome == RunOutcome.QUOTA_EXCEEDED
    assert len(ctx.list_artifacts(Sentiment)) == 1  # nothing new ran


def test_runtime_without_quota_is_unaffected():
    ctx = Context(resources=RuntimeResources(llm=FakeLLM('{"label": "ok"}')))
    ctx.create(Question(text="hi"))
    runtime = Runtime(ctx, agents=[_agent()])
    asyncio.run(runtime.arun())
    assert runtime.outcome == RunOutcome.COMPLETED
    assert len(ctx.list_artifacts(Sentiment)) == 1


class _CacheHitLLM(LLMProvider):
    """A provider that answers every call from the cache (never charged)."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="cached", raw={"reactifact_cache": "hit"})

    async def stream(self, request):
        yield LLMResponseChunk(text="cached")


def test_tracker_tokens_and_cost_limits():
    tokens = QuotaTracker(Quota(max_tokens=5))
    tokens.record("k", tokens=5)
    assert tokens.exceeded("k") is not None
    assert tokens.exceeded("k").limit == "max_tokens"  # type: ignore[union-attr]

    cost = QuotaTracker(Quota(max_cost=0.5))
    cost.record("k", cost=0.6)
    exceeded = cost.exceeded("k")
    assert exceeded is not None and exceeded.limit == "max_cost"


def test_tracker_reset_all_keys():
    tracker = QuotaTracker(Quota(max_calls=1))
    tracker.record("a", calls=1)
    tracker.record("b", calls=1)
    tracker.reset()
    assert tracker.exceeded("a") is None
    assert tracker.exceeded("b") is None


def test_tracker_usage_opens_a_fresh_window():
    now = [0.0]
    tracker = QuotaTracker(Quota(max_tokens=10, window_seconds=5), clock=lambda: now[0])
    tracker.record("k", tokens=3)
    assert tracker.usage("k").tokens == 3
    now[0] = 6.0
    assert tracker.usage("k").tokens == 0  # the window rolled over


def test_quota_llm_charges_with_a_pricer():
    tracker = QuotaTracker(Quota(max_cost=10.0))
    llm = QuotaLLM(
        FakeLLM("ok", usage={"prompt_tokens": 10, "completion_tokens": 5}),
        tracker,
        key="k",
        pricer=lambda model, prompt, completion: 0.02,
    )
    asyncio.run(llm.complete(LLMRequest(messages=[Message.user("hi")])))
    assert tracker.usage("k").cost == 0.02


def test_quota_llm_does_not_charge_a_cache_hit():
    tracker = QuotaTracker(Quota(max_calls=1))
    llm = QuotaLLM(_CacheHitLLM(), tracker, key="k")
    asyncio.run(llm.complete(LLMRequest(messages=[Message.user("hi")])))
    assert tracker.usage("k").calls == 0


def test_quota_llm_stream_counts_call_and_usage():
    tracker = QuotaTracker(Quota(max_tokens=100))
    llm = QuotaLLM(
        FakeLLM("hi", usage={"prompt_tokens": 4, "completion_tokens": 2}),
        tracker,
        key="k",
    )

    async def drain():
        return [chunk async for chunk in llm.stream(LLMRequest(messages=[]))]

    chunks = asyncio.run(drain())
    assert chunks
    assert tracker.usage("k").calls == 1
    assert tracker.usage("k").tokens == 6


def test_quota_llm_forwards_attributes():
    llm = QuotaLLM(FakeLLM("forwarded"), QuotaTracker(Quota()), key="k")
    assert llm.response == "forwarded"
