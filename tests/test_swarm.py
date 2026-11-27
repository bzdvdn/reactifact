"""Swarm recipe: peer handoffs, provenance, cycle and hop guards."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Agent, Context, Effects, Step
from reactifact.recipes.swarm import (
    Handoff,
    SwarmCycle,
    SwarmError,
    SwarmLimit,
    swarm,
)


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Note(BaseModel):
    text: str


class TriageStep(Step):
    consumes = (Question,)
    produces = (Handoff,)

    async def run(self, question: Question, effects: Effects) -> None:
        effects.create(
            Handoff(to="billing", reason="refund", payload={"text": question.text}),
            id="handoff:triage",
        )


class BillingStep(Step):
    consumes = (Handoff,)
    produces = (Answer,)

    async def run(self, handoff: Handoff) -> Answer:
        return Answer(text=f"billing: {handoff.payload['text']}")


def _agent(name: str, step: Step):
    return Agent(name, steps=[step])


def test_swarm_hands_off_and_records_provenance():
    async def go():
        return await swarm(
            Context(),
            groups={
                "triage": _agent("triage", TriageStep()),
                "billing": _agent("billing", BillingStep()),
            },
            start="triage",
            input=Question(text="refund"),
            final_type=Answer,
            max_hops=4,
        )

    result = asyncio.run(go())

    assert result.final is not None
    assert result.final.data.text == "billing: refund"
    assert len(result.handoffs) == 1
    handoff = result.handoffs[0].data
    assert handoff.from_agent == "triage"
    assert handoff.to == "billing"
    assert handoff.hop == 1
    assert len(result.run_ids) == 2
    assert len(result.context.relations()) == 2  # handoff_from + derived_from


class LoopAStep(Step):
    consumes = (Question, Handoff)
    produces = (Handoff,)

    async def run(self, effects: Effects) -> None:
        effects.create(Handoff(to="b", reason="again"), id="loop:a")


class LoopBStep(Step):
    consumes = (Handoff,)
    produces = (Handoff,)

    async def run(self, effects: Effects) -> None:
        effects.create(Handoff(to="a", reason="back"), id="loop:b")


def test_swarm_rejects_a_cycle_by_default():
    async def go():
        await swarm(
            Context(),
            groups={
                "a": _agent("a", LoopAStep()),
                "b": _agent("b", LoopBStep()),
            },
            start="a",
            input=Question(text="loop"),
            max_hops=10,
        )

    with pytest.raises(SwarmCycle, match="revisit"):
        asyncio.run(go())


def test_swarm_honours_max_hops_when_repeats_allowed():
    async def go():
        await swarm(
            Context(),
            groups={
                "a": _agent("a", LoopAStep()),
                "b": _agent("b", LoopBStep()),
            },
            start="a",
            input=Question(text="loop"),
            max_hops=2,
            allow_repeat=True,
        )

    with pytest.raises(SwarmLimit, match="max_hops=2"):
        asyncio.run(go())


def test_swarm_rejects_unknown_target():
    class MissingStep(Step):
        consumes = (Question,)
        produces = (Handoff,)

        async def run(self, effects: Effects) -> None:
            effects.create(Handoff(to="missing"), id="missing")

    async def go():
        await swarm(
            Context(),
            groups={"a": _agent("a", MissingStep())},
            start="a",
            input=Question(text="x"),
        )

    with pytest.raises(SwarmError, match="missing"):
        asyncio.run(go())


def test_swarm_requires_a_final_output():
    class NoteStep(Step):
        consumes = (Question,)
        produces = (Note,)

        async def run(self) -> Note:
            return Note(text="not final")

    async def go():
        await swarm(
            Context(),
            groups={"a": _agent("a", NoteStep())},
            start="a",
            input=Question(text="x"),
            final_type=Answer,
        )

    with pytest.raises(SwarmError, match="Answer"):
        asyncio.run(go())
