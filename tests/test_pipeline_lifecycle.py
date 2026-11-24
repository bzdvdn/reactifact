"""Pipeline lifespan and the in-memory test client."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic import BaseModel
from reactifact import FakeLLM, Pipeline, RuntimeResources, step


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
