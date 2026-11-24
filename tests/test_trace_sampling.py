"""Tail sampling: keep failures, keep a deterministic slice of successes."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import Pipeline, step
from reactifact.tracing import RunTrace, SamplingPolicy, Tracer, TraceStore


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(consumes=Question)
def answer(question: Question) -> Answer:
    return Answer(text=question.text)


def _trace(run_id: str, *, outcome: str = "completed", duration: float = 1.0):
    return RunTrace(id=run_id, outcome=outcome, duration_ms=duration)


def _export(tracer: Tracer, trace: RunTrace) -> None:
    asyncio.run(tracer.on_turn_end(trace))


def test_failures_are_always_kept_and_successes_can_be_dropped(tmp_path):
    store = TraceStore(str(tmp_path / "t.db"))
    tracer = Tracer(store=store, sample=SamplingPolicy(rate=0.0))

    _export(tracer, _trace("ok"))
    _export(tracer, _trace("bad", outcome="failed"))

    items = asyncio.run(store.query())["items"]
    assert [item["id"] for item in items] == ["bad"]
    store.close()


def test_always_if_keeps_slow_runs():
    policy = SamplingPolicy(rate=0.0, always_if=lambda t: t.duration_ms > 1000)
    assert policy.should_sample(_trace("slow", duration=2000)) is True
    assert policy.should_sample(_trace("fast", duration=10)) is False


def test_rate_is_deterministic_per_run_id():
    policy = SamplingPolicy(rate=0.5, always_on_error=False)
    assert policy.should_sample(_trace("same")) == policy.should_sample(_trace("same"))


def test_pipeline_trace_sample_zero_drops_successful_runs():
    pipeline = Pipeline(
        steps=[answer],
        traces=True,
        trace_sample=SamplingPolicy(rate=0.0),
    )
    result = pipeline.run(Question(text="hi"))
    assert result.trace_id
    assert asyncio.run(result.trace()) is None
    assert asyncio.run(pipeline.trace_store.query())["total"] == 0
    pipeline.close()
