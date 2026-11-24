"""Typed resource injection and Pipeline overrides (FastAPI-style DI)."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import (
    FakeLLM,
    LLMProvider,
    Pipeline,
    RuntimeResources,
    step,
)


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


@step(consumes=Question)
def with_llm(question: Question, llm: LLMProvider) -> Answer:
    return Answer(text=f"{type(llm).__name__}:{question.text}")


def test_registered_typed_resource_is_injected():
    resources = RuntimeResources()
    resources.register(Service, Service("base"))
    result = Pipeline(steps=[with_service], resources=resources).run(Question(text="q"))
    assert result.latest(Answer).data.text == "base:q"


def test_pipeline_override_replaces_a_resource():
    resources = RuntimeResources()
    resources.register(Service, Service("base"))
    pipeline = Pipeline(steps=[with_service], resources=resources).override(
        Service, Service("override")
    )
    result = pipeline.run(Question(text="q"))
    assert result.latest(Answer).data.text == "override:q"

    pipeline.clear_overrides()
    result = pipeline.run(Question(text="q"))
    assert result.latest(Answer).data.text == "base:q"


def test_builtin_llm_is_resolved_by_protocol():
    resources = RuntimeResources(llm=FakeLLM())
    result = Pipeline(steps=[with_llm], resources=resources).run(Question(text="q"))
    assert result.latest(Answer).data.text.startswith("FakeLLM:")


def test_pipeline_llm_shortcut():
    result = Pipeline(steps=[with_llm], llm=FakeLLM()).run(Question(text="q"))
    assert result.latest(Answer).data.text.startswith("FakeLLM:")


class OverrideLLM(FakeLLM):
    pass


def test_override_wins_over_the_pipeline_llm():
    pipeline = Pipeline(steps=[with_llm], llm=FakeLLM()).override(
        LLMProvider, OverrideLLM()
    )
    result = pipeline.run(Question(text="q"))
    assert result.latest(Answer).data.text.startswith("OverrideLLM:")
