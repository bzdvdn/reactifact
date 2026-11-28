"""Typed resource injection and Pipeline overrides (FastAPI-style DI)."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import (
    FakeLLM,
    LLMProvider,
    Pipeline,
    PipelineRunner,
    RunConfig,
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


@step(reads=Question)
def with_service(question: Question, service: Service) -> Answer:
    return Answer(text=f"{service.name}:{question.text}")


@step(reads=Question)
def with_llm(question: Question, llm: LLMProvider) -> Answer:
    return Answer(text=f"{type(llm).__name__}:{question.text}")


def test_registered_typed_resource_is_injected():
    resources = RuntimeResources()
    resources.register(Service, Service("base"))
    result = PipelineRunner(
        Pipeline(steps=[with_service]), config=RunConfig(resources=resources)
    ).run(Question(text="q"))
    assert result.latest(Answer).data.text == "base:q"


def test_pipeline_override_replaces_a_resource():
    resources = RuntimeResources()
    resources.register(Service, Service("base"))
    runner = PipelineRunner(
        Pipeline(steps=[with_service]), config=RunConfig(resources=resources)
    ).override(Service, Service("override"))
    result = runner.run(Question(text="q"))
    assert result.latest(Answer).data.text == "override:q"

    runner.clear_overrides()
    result = runner.run(Question(text="q"))
    assert result.latest(Answer).data.text == "base:q"


def test_builtin_llm_is_resolved_by_protocol():
    resources = RuntimeResources(llm=FakeLLM())
    result = PipelineRunner(
        Pipeline(steps=[with_llm]), config=RunConfig(resources=resources)
    ).run(Question(text="q"))
    assert result.latest(Answer).data.text.startswith("FakeLLM:")


def test_pipeline_llm_shortcut():
    result = PipelineRunner(
        Pipeline(steps=[with_llm]), config=RunConfig(llm=FakeLLM())
    ).run(Question(text="q"))
    assert result.latest(Answer).data.text.startswith("FakeLLM:")


class OverrideLLM(FakeLLM):
    pass


def test_override_wins_over_the_pipeline_llm():
    runner = PipelineRunner(
        Pipeline(steps=[with_llm]), config=RunConfig(llm=FakeLLM())
    ).override(LLMProvider, OverrideLLM())
    result = runner.run(Question(text="q"))
    assert result.latest(Answer).data.text.startswith("OverrideLLM:")
