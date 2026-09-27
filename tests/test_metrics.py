"""`reactifact.metrics` — dependency-free Prometheus metrics: the collector,
the tracer hook, and the `/metrics` router."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Produce, ProduceCall, Runtime
from reactifact.agents import create_agent
from reactifact.metrics import Metrics, MetricsTracer, create_metrics_router
from reactifact.resources import RuntimeResources
from reactifact.tracing.models import (
    AgentSpan,
    ArtifactRef,
    LLMCall,
    RelationRef,
    RunTrace,
)


def _call(**kwargs) -> LLMCall:
    base = {
        "provider": "p",
        "model": "m",
        "agent": "a",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "latency_ms": 20.0,
    }
    return LLMCall(**{**base, **kwargs})


def test_counters_and_histograms_render():
    metrics = Metrics()
    metrics.increment("foo_total", 2, x="a")
    metrics.observe("bar_seconds", 0.03, x="a")

    text = metrics.render()

    assert "# TYPE foo_total counter" in text
    assert 'foo_total{x="a"} 2' in text
    assert "# TYPE bar_seconds histogram" in text
    assert 'bar_seconds_bucket{x="a",le="0.05"} 1' in text
    assert 'bar_seconds_bucket{x="a",le="+Inf"} 1' in text
    assert 'bar_seconds_count{x="a"} 1' in text
    assert 'bar_seconds_sum{x="a"} 0.03' in text


def test_label_values_are_escaped():
    metrics = Metrics()
    metrics.increment("x_total", a='he said "hi"\nnext')
    assert 'a="he said \\"hi\\"\\nnext"' in metrics.render()


def test_metrics_tracer_records_a_turn():
    metrics = Metrics()
    trace = RunTrace(
        outcome="completed",
        spans=[
            AgentSpan(agent="a", latency_ms=1500.0, llm_calls=[_call()]),
        ],
    )

    asyncio.run(MetricsTracer(metrics).on_turn_end(trace))

    text = metrics.render()
    assert 'reactifact_runs_total{outcome="completed"} 1' in text
    assert 'reactifact_agent_runs_total{agent="a",status="ok"} 1' in text
    assert 'reactifact_agent_latency_seconds_bucket{agent="a",le="2.5"} 1' in text
    assert 'reactifact_llm_calls_total{agent="a",model="m",provider="p"} 1' in text
    assert (
        'reactifact_llm_tokens_total{kind="prompt",model="m",provider="p"} 10' in text
    )
    assert (
        'reactifact_llm_tokens_total{kind="completion",model="m",provider="p"} 5'
        in text
    )


def test_budget_and_error_metrics():
    metrics = Metrics()
    trace = RunTrace(
        outcome="budget_tokens_exceeded",
        spans=[
            AgentSpan(agent="a", error="boom", llm_calls=[_call(error="rate limited")]),
        ],
    )

    asyncio.run(MetricsTracer(metrics).on_turn_end(trace))

    text = metrics.render()
    assert (
        'reactifact_budget_exceeded_total{outcome="budget_tokens_exceeded"} 1' in text
    )
    assert 'reactifact_agent_runs_total{agent="a",status="error"} 1' in text
    assert 'reactifact_llm_errors_total{model="m",provider="p"} 1' in text


def test_cost_metric_needs_a_pricer():
    def pricer(model: str, prompt: int, completion: int) -> float:
        return (prompt + completion) * 0.001

    trace = RunTrace(
        outcome="completed", spans=[AgentSpan(agent="a", llm_calls=[_call()])]
    )

    with_pricer = Metrics()
    asyncio.run(MetricsTracer(with_pricer, pricer=pricer).on_turn_end(trace))
    assert (
        'reactifact_llm_cost_total{model="m",provider="p"} 0.015'
        in with_pricer.render()
    )

    without = Metrics()
    asyncio.run(MetricsTracer(without).on_turn_end(trace))
    assert "reactifact_llm_cost_total" not in without.render()


def test_create_metrics_router_serves_prometheus_text():
    metrics = Metrics()
    metrics.increment("reactifact_runs_total", outcome="completed")
    app = FastAPI()
    app.include_router(create_metrics_router(metrics))

    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert 'reactifact_runs_total{outcome="completed"} 1' in response.text


def test_artifact_and_relation_metrics_come_from_the_spans():
    metrics = Metrics()
    trace = RunTrace(
        outcome="completed",
        spans=[
            AgentSpan(
                agent="answer",
                reads=[ArtifactRef(artifact_id="q1", data_type="Question")],
                writes=[
                    ArtifactRef(
                        artifact_id="a1", op_type="create", data_type="FinalResponse"
                    )
                ],
                relations=[
                    RelationRef(source_id="a1", relation="supported_by", target_id="e1")
                ],
            )
        ],
    )

    asyncio.run(MetricsTracer(metrics).on_turn_end(trace))

    text = metrics.render()
    assert (
        'reactifact_artifacts_written_total{op="create",type="FinalResponse"} 1' in text
    )
    assert 'reactifact_artifacts_read_total{type="Question"} 1' in text
    assert 'reactifact_relations_total{relation="supported_by"} 1' in text


def test_domain_metrics_via_the_resources_sink():
    metrics = Metrics()
    resources = RuntimeResources(metrics=metrics)

    resources.metrics.increment("route_total", route="read_sources")
    resources.metrics.observe("docs_found", 3.0, source="confluence")

    text = metrics.render()
    assert 'route_total{route="read_sources"} 1' in text
    assert 'docs_found_count{source="confluence"} 1' in text

    # Unconfigured resources: the sink is a no-op, never a None.
    RuntimeResources().metrics.increment("ignored_total")


class Number(BaseModel):
    value: int


class Bump(Produce[Number]):
    artifact_type = Number

    async def produce(self, call: ProduceCall) -> None:
        trigger = call.trigger
        if trigger is not None and trigger.data.value < 2:
            call.effects.create(Number(value=trigger.data.value + 1))
            call.context.resources.metrics.increment("test_bumped_total")


def test_metrics_tracer_runs_inside_a_runtime():
    agent: Agent = create_agent("bump", consumes=[Consume(Number)], produces=[Bump()])
    metrics = Metrics()
    ctx = Context(resources=RuntimeResources(metrics=metrics))
    ctx.create(Number(value=0))

    asyncio.run(Runtime(ctx, agents=[agent], tracer=MetricsTracer(metrics)).arun())

    text = metrics.render()
    assert 'reactifact_runs_total{outcome="completed"} 1' in text
    # 2 traced executions (the settling generation produced no patch, so no span)
    assert 'reactifact_agent_runs_total{agent="bump",status="ok"} 2' in text
    # a domain counter recorded by the produce via resources.metrics
    assert "test_bumped_total 2" in text
