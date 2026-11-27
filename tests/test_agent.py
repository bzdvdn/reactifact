"""`Agent` and the `@agent` decorator: the small and big authoring forms."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Agent, Consume, Context, EventType, Read, Runtime, Step, step
from reactifact.agent import agent
from reactifact.commit import CommitRead
from reactifact.commit import Read as CommitReadAlias


class Question(BaseModel):
    text: str


class Upper(BaseModel):
    text: str


class Lower(BaseModel):
    text: str


class UpperStep(Step):
    reads = (Question,)
    produces = (Upper,)

    async def run(self, question: Question) -> Upper:
        return Upper(text=question.text.upper())


class LowerStep(Step):
    reads = (Question,)
    produces = (Lower,)

    async def run(self, question: Question) -> Lower:
        return Lower(text=question.text.lower())


def test_step_reads_are_the_input_contract():
    @step(produces=Upper, reads=Question)
    def upper(question: Question) -> Upper:
        return Upper(text=question.text.upper())

    assert upper.reads is not None
    assert upper.reads[0].artifact_type is Question
    assert upper.read_specs()[0].artifact_type is Question
    assert upper.reads[0].required is True


def test_typed_parameters_are_automatic_reads():
    @step
    def upper(question: Question) -> Upper:
        return Upper(text=question.text.upper())

    assert upper.reads is not None
    assert upper.reads[0].artifact_type is Question


def test_authoring_package_layout():
    import importlib

    agent_pkg = importlib.import_module("reactifact.agent")
    step_pkg = importlib.import_module("reactifact.agent.step")
    legacy = importlib.import_module("reactifact.step")

    assert agent_pkg.Agent is Agent
    assert step_pkg.Step is Step
    assert legacy.Step is Step
    assert legacy.Agent is Agent


def test_read_and_consume_share_selector_behavior():
    from reactifact.selector import Selector

    read = Read.by_field(Question, "text", "hello")
    consume = Consume.on_create(Question)

    assert isinstance(read, Selector)
    assert isinstance(consume, Selector)
    assert read.artifact_type is Question
    assert consume.event_types == [EventType.ARTIFACT_CREATED]
    assert read.required is True
    assert read.to_consume().wakes is True
    assert Read(Question, required=False).to_consume().wakes is False
    assert not hasattr(Read, "on_delete")


def test_read_after_delete_is_event_only():
    read = Read.after_delete(Question)

    assert read.event_types == [EventType.ARTIFACT_DELETED]
    assert read.required is True
    assert read.collect(Context()) == []


def test_commit_read_was_renamed():
    assert CommitReadAlias is CommitRead


def test_big_agent_runs_its_steps_together():
    agent = Agent(
        "shout",
        consumes=[Question],
        steps=[UpperStep(), LowerStep()],
    )
    context = Context()
    context.create(Question(text="Hello"))

    asyncio.run(Runtime(context, agents=[agent]).arun())

    assert context.latest(Upper).data.text == "HELLO"
    assert context.latest(Lower).data.text == "hello"


def test_agent_subclass_can_declare_steps_as_methods():
    class QAAgent(Agent):
        name = "qa"
        consumes = [Consume(Question)]

        def __init__(self, suffix: str):
            self.suffix = suffix
            super().__init__()

        @step(produces=Upper)
        async def upper(self, question: Question) -> Upper:
            return Upper(text=question.text.upper() + self.suffix)

    phase = QAAgent("!")

    assert phase.steps is not None
    assert len(phase.steps) == 1
    assert phase.upper._owner is phase

    context = Context()
    context.create(Question(text="hello"))

    asyncio.run(Runtime(context, agents=[phase]).arun())

    assert context.latest(Upper).data.text == "HELLO!"


def test_agent_decorator_builds_one_step_agent():
    @agent
    async def upper(question: Question) -> Upper:
        return Upper(text=question.text.upper())

    assert isinstance(upper, Agent)
    assert upper.name == "upper"

    context = Context()
    context.create(Question(text="hello"))

    asyncio.run(Runtime(context, agents=[upper]).arun())

    assert context.latest(Upper).data.text == "HELLO"


def test_agent_decorator_accepts_explicit_consumes_and_policy():
    @agent(name="lower", consumes=[Consume(Question)], priority=5)
    async def lower(question: Question) -> Lower:
        return Lower(text=question.text.lower())

    assert lower.name == "lower"
    assert lower.priority == 5
    assert lower.consumes is not None
    assert lower.consumes[0].artifact_type is Question


def test_agent_decorator_requires_a_consumed_artifact():
    with pytest.raises(TypeError, match="needs at least one consumed artifact"):

        @agent
        async def blind() -> Upper:
            return Upper(text="no input")
