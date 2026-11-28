"""Step guards: pre/post evaluation, composition and runtime integration."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    ArtifactKey,
    Consume,
    Context,
    Guard,
    GuardResult,
    Runtime,
    RuntimeResources,
    Step,
    StepInvocation,
    step,
)
from reactifact.agent.step.resolve import MissingInputError


class Turn(BaseModel):
    query_id: str
    text: str = ""
    status: str = "researching"


class Evidence(BaseModel):
    query_id: str
    text: str


class Answer(BaseModel):
    query_id: str
    text: str


AnswerKey = ArtifactKey(Answer, prefix="answer", field="query_id")


def test_idempotent_guard_checks_the_key_target():
    context = Context(resources=RuntimeResources())
    turn = context.create(Turn(query_id="q1"))
    guard = Guard.idempotent(AnswerKey)

    assert asyncio.run(guard.pre(context, None, turn)).allowed

    context.create(Answer(query_id="q1", text="old"), id=AnswerKey("q1"))
    result = asyncio.run(guard.pre(context, None, turn))
    assert result.allowed is False
    assert result.code == "idempotent"
    assert "answer:q1" in result.reason


def test_field_guard_reads_the_trigger_data():
    context = Context(resources=RuntimeResources())
    turn = context.create(Turn(query_id="q1", status="answerable"))
    guard = Guard.field("status", "answerable")

    assert asyncio.run(guard.pre(context, None, turn)).allowed
    turn.data.status = "researching"  # type: ignore[misc]
    assert asyncio.run(guard.pre(context, None, turn)).allowed is False


def test_present_guard_checks_post_inputs():
    context = Context(resources=RuntimeResources())
    turn = context.create(Turn(query_id="q1"))
    evidence = context.create(Evidence(query_id="q1", text="fact"))
    call = StepInvocation(context=context, inputs=[evidence], trigger=turn)

    assert asyncio.run(Guard.ready(Evidence).post(call)).allowed
    assert asyncio.run(Guard.has(Evidence).post(call)).allowed
    empty = StepInvocation(context=context, inputs=[], trigger=turn)
    assert asyncio.run(Guard.ready(Evidence).post(empty)).allowed is False


def test_guard_composition_and_not():
    context = Context(resources=RuntimeResources())
    turn = context.create(Turn(query_id="q1", status="answerable"))

    combined = Guard.all(
        Guard.field("status", "answerable"),
        Guard.not_(Guard.field("status", "other")),
    )
    assert asyncio.run(combined.pre(context, None, turn)).allowed

    either = Guard.any(
        Guard.field("status", "other"),
        Guard.field("status", "answerable"),
    )
    assert asyncio.run(either.pre(context, None, turn)).allowed


def test_custom_guard_returns_bool_or_result():
    context = Context(resources=RuntimeResources())
    turn = context.create(Turn(query_id="q1"))

    assert asyncio.run(
        Guard.custom(lambda ctx, event, trigger: True).pre(context, None, turn)
    ).allowed
    denied = Guard.custom(lambda ctx, event, trigger: GuardResult.deny("nope"))
    assert asyncio.run(denied.pre(context, None, turn)).allowed is False


class AnswerStep(Step[Answer]):
    reads = (Turn, Evidence)
    guard = Guard.idempotent(AnswerKey)

    async def run(self, evidence: Evidence) -> Answer:
        return Answer(query_id=evidence.query_id, text=evidence.text)


def test_pre_guard_runs_before_required_inputs():
    """An existing target skips the step before resolving its Evidence input."""
    context = Context(resources=RuntimeResources())
    existing = context.create(Answer(query_id="q1", text="old"), id=AnswerKey("q1"))
    context.create(Turn(query_id="q1"))

    runtime = Runtime(
        context,
        agents=[Agent("answer", consumes=[Consume(Turn)], steps=[AnswerStep()])],
    )
    asyncio.run(runtime.arun())

    assert context.get(AnswerKey("q1")) is existing
    assert len(context.list_artifacts(Answer)) == 1


def test_pre_guard_does_not_hide_a_real_missing_input():
    context = Context(resources=RuntimeResources())
    context.create(Turn(query_id="q1"))

    runtime = Runtime(
        context,
        agents=[Agent("answer", consumes=[Consume(Turn)], steps=[AnswerStep()])],
    )
    with pytest.raises(MissingInputError):
        asyncio.run(runtime.arun())


class NeedsEvidence(Step[Answer]):
    reads = (Turn, Evidence)
    guard = Guard.ready(Evidence)

    async def run(self, turn: Turn, evidence: Evidence) -> Answer:
        return Answer(query_id=turn.query_id, text=evidence.text)


def test_post_guard_skips_when_inputs_are_incomplete():
    context = Context(resources=RuntimeResources())
    context.create(Turn(query_id="q1"))

    runtime = Runtime(
        context,
        agents=[
            Agent(
                "needs-evidence",
                consumes=[Consume(Turn), Consume(Evidence)],
                steps=[NeedsEvidence()],
            )
        ],
    )
    asyncio.run(runtime.arun())

    assert context.list_artifacts(Answer) == []


def test_step_decorator_accepts_a_guard():
    @step(produces=Answer, reads=Turn, guard=Guard.field("status", "answerable"))
    async def answer(turn: Turn) -> Answer:
        return Answer(query_id=turn.query_id, text="ok")

    assert answer.guard is not None

    context = Context(resources=RuntimeResources())
    turn = context.create(Turn(query_id="q1", status="answerable"))
    asyncio.run(answer.guard.pre(context, None, turn))
