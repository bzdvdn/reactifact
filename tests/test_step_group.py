"""StepGroup is the aggregate; steps are the model."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Artifact,
    Budget,
    Consume,
    Context,
    Emit,
    Pipeline,
    RetryPolicy,
    Runtime,
    StepGroup,
    create_step_group,
    step,
)
from reactifact.quick import chat_agent


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(produces=Answer, consumes=Question)
def answer(question: Question) -> Answer:
    return Answer(text=question.text)


@step(
    produces=Answer,
    consumes=Question,
    name="analysis",
    priority=5,
    concurrency_limit=2,
)
def named(question: Question) -> Answer:
    return Answer(text=question.text)


def test_legacy_grouping_names_stay_importable() -> None:
    import importlib

    step_module = importlib.import_module("reactifact.step")
    assert StepGroup is Agent
    assert step_module.StepGroup is Agent  # type: ignore[attr-defined]
    assert "StepGroup" not in step_module.__all__
    assert callable(create_step_group)
    assert "create_step_group" not in step_module.__all__


def test_create_step_group_builds_a_group() -> None:
    group = create_step_group("qa", consumes=[Consume(Question)], steps=[answer])
    assert isinstance(group, StepGroup)
    assert group.name == "qa"
    assert group.steps == [answer]
    assert group.consumes is not None


def test_subclassing_step_group_still_works() -> None:
    class MyGroup(StepGroup):
        name = "mine"

    group = MyGroup(steps=[answer])
    assert isinstance(group, StepGroup)
    assert group.name == "mine"


def test_runtime_accepts_groups_keyword() -> None:
    group = create_step_group("qa", steps=[answer])
    context = Context()
    runtime = Runtime(context, groups=[group], budget=Budget(max_runs=5))
    assert runtime.groups == [group]


def test_runtime_accepts_raw_steps_and_wraps_each_one() -> None:
    context = Context()
    context.create(Question(text="hi"))
    runtime = Runtime(context, steps=[answer])
    runtime.run()
    assert context.latest(Answer) is not None
    assert [group.name for group in runtime.groups] == ["answer"]


def test_runtime_merges_steps_and_groups_in_order() -> None:
    context = Context()
    group = create_step_group("explicit", steps=[answer])
    runtime = Runtime(context, steps=[answer], groups=[group])
    assert [unit.name for unit in runtime.groups] == ["answer", "explicit"]


def test_runtime_rejects_wrong_types_with_a_useful_message() -> None:
    group = create_step_group("g", steps=[answer])
    with pytest.raises(TypeError, match="steps= accepts Step"):
        Runtime(Context(), steps=[group])
    with pytest.raises(TypeError, match="agents= accepts Agent"):
        Runtime(Context(), groups=[answer])


def test_step_name_and_policy_are_copied_to_the_implicit_group() -> None:
    runtime = Runtime(Context(), steps=[named])
    group = runtime.groups[0]
    assert group.name == "analysis"
    assert group.priority == 5
    assert group.concurrency_limit == 2
    assert named.name == "analysis"


@step(produces=Answer, consumes=Question)
def emit_answer(question: Artifact[Question]) -> Emit:
    return Emit(
        Answer(text=question.data.text),
        id="custom-answer",
        links=(("supported_by", question),),
    )


@step(produces=Answer, consumes=Question, retry=RetryPolicy(max_attempts=2))
def retrying(question: Question) -> Answer:
    return Answer(text=question.text)


def test_emit_sets_an_explicit_id_and_links() -> None:
    context = Context()
    context.create(Question(text="hi"), id="q")
    Runtime(context, steps=[emit_answer]).run()
    answer = context.get("custom-answer")
    assert answer is not None
    assert context.related("custom-answer", "supported_by")[0].id == "q"


def test_step_retry_is_copied_to_the_implicit_group() -> None:
    runtime = Runtime(Context(), steps=[retrying])
    assert isinstance(runtime.groups[0].retry, RetryPolicy)


def test_pipeline_run_returns_a_friendly_result() -> None:
    result = Pipeline(steps=[answer]).run(Question(text="hi"))
    assert result.latest(Answer).data.text == "hi"
    assert result.runs >= 1
    assert len(result.hash) == 64


def test_chat_agent_accepts_raw_steps() -> None:
    assert chat_agent(steps=[answer]) is not None


def test_pipeline_arun_and_astream() -> None:
    pipeline = Pipeline(steps=[answer])
    result = asyncio.run(pipeline.arun(Question(text="async")))
    assert result.latest(Answer).data.text == "async"

    async def consume() -> int:
        count = 0
        async for _event in pipeline.astream(Question(text="stream")):
            count += 1
        return count

    assert asyncio.run(consume()) >= 1
    assert pipeline.last_result is not None
