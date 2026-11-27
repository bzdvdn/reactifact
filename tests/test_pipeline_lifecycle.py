"""Pipeline lifespan and the in-memory test client."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic import BaseModel
from reactifact import (
    FakeLLM,
    Pipeline,
    PipelineRunner,
    RunConfig,
    RuntimeResources,
    step,
)
from reactifact.tracing import SamplingPolicy


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Service:
    def __init__(self, name: str):
        self.name = name


@step(consumes=Question)
def with_service(question: Question, service: Service) -> Answer:
    return Answer(text=f"{service.name}:{question.text}")


def test_lifespan_resources_are_entered_and_closed():
    closed: list[bool] = []

    @asynccontextmanager
    async def lifespan():
        resources = RuntimeResources()
        resources.register(Service, Service("lifespan"))
        yield resources
        closed.append(True)

    pipeline = Pipeline(steps=[with_service], lifespan=lifespan)

    async def run() -> Answer | None:
        async with pipeline:
            result = await pipeline.arun(Question(text="q"))
            return result.latest(Answer)

    answer = asyncio.run(run())
    assert answer is not None
    assert answer.data.text == "lifespan:q"
    assert closed == [True]


def test_sync_run_requires_entering_a_lifespan_pipeline():
    @asynccontextmanager
    async def lifespan():
        yield RuntimeResources()

    pipeline = Pipeline(steps=[with_service], lifespan=lifespan)
    with pytest.raises(RuntimeError, match="async with"):
        pipeline.run(Question(text="q"))


def test_test_client_has_an_in_memory_trace_store():
    client = Pipeline(steps=[with_service], llm=FakeLLM()).test_client()
    client.override(Service, Service("test"))
    result = client.run(Question(text="q"))
    assert result.latest(Answer).data.text == "test:q"
    assert client.traces is not None
    traces = asyncio.run(client.traces.query())
    assert traces["total"] >= 1
    client.close()


def test_pipeline_runner_accepts_run_config():
    pipeline = Pipeline(steps=[with_service], seed_type=Question, answer_type=Answer)
    runner = PipelineRunner(
        pipeline,
        config=RunConfig(
            resources=RuntimeResources().with_overrides({Service: Service("config")}),
            traces=True,
        ),
    )

    result = runner.run(Question(text="q"))

    assert result.latest(Answer).data.text == "config:q"
    assert runner.config is not None
    assert runner.trace_store is not None


def test_pipeline_answer_returns_the_typed_model():
    pipeline = Pipeline(
        steps=[with_service],
        seed_type=Question,
        answer_type=Answer,
        resources=RuntimeResources().with_overrides({Service: Service("typed")}),
    )
    assert pipeline.answer(Question(text="q")).text == "typed:q"
    assert asyncio.run(pipeline.aanswer(Question(text="a"))).text == "typed:a"


def test_test_pipeline_answer_and_trace_sample():
    pipeline = Pipeline(
        steps=[with_service],
        answer_type=Answer,
        resources=RuntimeResources().with_overrides({Service: Service("test")}),
        traces=True,
        trace_sample=SamplingPolicy(rate=0.0),
    )
    with pipeline.test_client() as client:
        assert client.answer(Question(text="q")).text == "test:q"
        assert client.traces is not None
        assert asyncio.run(client.traces.query())["total"] == 0
