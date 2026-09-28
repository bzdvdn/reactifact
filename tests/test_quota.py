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
from reactifact.providers.contracts import LLMRequest, Message
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
