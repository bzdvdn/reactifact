"""`Agent`/structured `Pipeline`/`PipelineRunner` authoring layer."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Chat,
    Consume,
    Context,
    Pipeline,
    PipelineRunner,
    RetryPolicy,
    Runtime,
    Step,
)
from reactifact.recipes import Route


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Answerer(Step[Answer]):
    reads = (Consume(Question),)

    async def run(self, question: Question) -> Answer:
        return Answer(text=question.text.upper())


def test_agent_with_steps_compiles_to_one_group():
    retry = RetryPolicy(max_attempts=2)
    agent = Agent(
        "main",
        steps=[Answerer()],
        consumes=[Consume(Question)],
        retry=retry,
        priority=3,
        concurrency_limit=2,
        capabilities=("llm",),
    )

    (group,) = agent.agents()

    assert agent.agents()[0] is group
    assert group.name == "main"
    assert group.consumes is not None
    assert group.consumes[0].artifact_type is Question
    assert group.retry is retry
    assert group.priority == 3
    assert group.concurrency_limit == 2
    assert group.capabilities == ("llm",)


def test_agent_rejects_empty_steps():
    with pytest.raises(ValueError, match="at least one step"):
        Agent("bad", steps=[])


def test_pipeline_expands_agents_and_routes():
    route = Route(
        on=Question,
        key=lambda question: "main",
        cases={"main": Agent("route_case", steps=[Answerer()])},
        name="qa",
    )
    pipeline = Pipeline(
        name="qa",
        agents=[Agent("main", steps=[Answerer()])],
        routes=[route],
        seed_type=Question,
        answer_type=Answer,
    )

    assert pipeline.name == "qa"
    assert pipeline.steps is None
    assert [agent.name for agent in pipeline.agents] == ["main", "qa:main"]
    assert "flowchart LR" in pipeline.mermaid()
    assert pipeline.validate().ok


def test_pipeline_rejects_raw_and_structured_sources_together():
    with pytest.raises(TypeError, match="not both"):
        Pipeline(
            steps=[Answerer()],
            agents=[Agent("main", steps=[Answerer()])],
        )


def test_pipeline_runner_runs_a_structured_pipeline():
    pipeline = Pipeline(
        name="qa",
        agents=[Agent("main", steps=[Answerer()])],
        seed_type=Question,
        answer_type=Answer,
    )
    runner = PipelineRunner(pipeline)

    result = runner.run(Question(text="hello"))

    assert result.context.latest(Answer).data.text == "HELLO"
    assert runner.last_result is result
    assert runner.answer(Question(text="bye")).text == "BYE"
    assert runner.describe().seed_schema is not None


def test_pipeline_runner_does_not_change_pipeline_running():
    group = Agent("group", steps=[Answerer()])
    pipeline = Pipeline(agents=[group], seed_type=Question, answer_type=Answer)
    runner = PipelineRunner(pipeline)

    assert runner.run(Question(text="x")).context.latest(Answer).data.text == "X"
    assert (
        PipelineRunner(pipeline)
        .run(Question(text="y"))
        .context.latest(Answer)
        .data.text
        == "Y"
    )


def test_runtime_accepts_agents():
    agent = Agent("main", steps=[Answerer()])
    context = Context()
    context.create(Question(text="hello"))

    asyncio.run(Runtime(context, agents=[agent]).arun())

    assert context.latest(Answer).data.text == "HELLO"


def test_chat_accepts_agents():
    chat = Chat(agents=[Agent("main", steps=[Answerer()])], seed_type=Question)

    assert asyncio.run(chat.invoke("hello", session_id="s1"))["reply"] == "HELLO"
    assert chat.agents is not None
    assert chat.agents[0].name == "main"
    assert chat.agents == chat.agents


def test_tool_agent_constructor_takes_keywords():
    from reactifact.llm_agent import HITLLMAgent

    agent = HITLLMAgent(
        name="qa",
        system="answer briefly",
        tools=[],
        consumes=[Consume(Question)],
        steps=[Answerer()],
        max_steps=3,
        max_asks=1,
        temperature=0.2,
        resume_announce=lambda answer: f"Accepted: {answer}",
    )

    assert isinstance(agent, Agent)
    assert agent.name == "qa"
    assert agent.system == "answer briefly"
    assert agent.max_steps == 3
    assert agent.max_asks == 1
    assert agent.temperature == 0.2
    assert agent.steps is not None and len(agent.steps) == 2  # tool loop + answerer
    assert agent.resume_announce is not None


def test_chat_accepts_a_pipeline():
    pipeline = Pipeline(
        name="qa",
        agents=[Agent("main", steps=[Answerer()])],
        seed_type=Question,
        answer_type=Answer,
    )
    chat = Chat(pipeline=pipeline)

    reply = asyncio.run(chat.invoke("hello", session_id="s1"))

    assert reply["reply"] == "HELLO"
    assert asyncio.run(chat.answer("typed", session_id="s2")).text == "TYPED"
