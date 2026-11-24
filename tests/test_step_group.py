"""StepGroup is the name; Agent is a compatibility alias."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Budget,
    Consume,
    Context,
    Runtime,
    StepGroup,
    create_agent,
    create_step_group,
    step,
)


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


def test_agent_is_an_alias_for_step_group() -> None:
    assert Agent is StepGroup
    assert create_agent is create_step_group


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
    assert runtime.agents == [group]


def test_runtime_accepts_raw_steps_and_wraps_each_one() -> None:
    context = Context()
    context.create(Question(text="hi"))
    runtime = Runtime(context, steps=[answer])
    runtime.run()
    assert context.latest(Answer) is not None
    assert [group.name for group in runtime.groups] == ["answer"]
    assert runtime.groups is runtime.agents


def test_runtime_merges_steps_and_groups_in_order() -> None:
    context = Context()
    group = create_step_group("explicit", steps=[answer])
    runtime = Runtime(context, steps=[answer], groups=[group])
    assert [unit.name for unit in runtime.groups] == ["answer", "explicit"]


def test_runtime_rejects_wrong_types_with_a_useful_message() -> None:
    group = create_step_group("g", steps=[answer])
    with pytest.raises(TypeError, match="steps= accepts Step"):
        Runtime(Context(), steps=[group])
    with pytest.raises(TypeError, match="groups= accepts StepGroup"):
        Runtime(Context(), groups=[answer])
    with pytest.raises(TypeError, match="agents= accepts StepGroup"):
        Runtime(Context(), agents=[answer])


def test_runtime_rejects_agents_together_with_new_keywords() -> None:
    group = create_step_group("g", steps=[answer])
    with pytest.raises(ValueError, match="not both"):
        Runtime(Context(), agents=[group], steps=[answer])
    with pytest.raises(ValueError, match="not both"):
        Runtime(Context(), agents=[group], groups=[group])


def test_step_name_and_policy_are_copied_to_the_implicit_group() -> None:
    runtime = Runtime(Context(), steps=[named])
    group = runtime.groups[0]
    assert group.name == "analysis"
    assert group.priority == 5
    assert group.concurrency_limit == 2
    assert named.name == "analysis"
