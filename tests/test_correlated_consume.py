import asyncio

from pydantic import BaseModel
from reactifact import Agent, Context, Produce, Runtime
from reactifact.consume import CorrelatedConsume


class Report(BaseModel):
    thread_id: str


class PendingQuestion(BaseModel):
    thread_id: str
    answered: bool = False


class HelpdeskTicket(BaseModel):
    thread_id: str


class Approval(BaseModel):
    parts: list[str]


class RecordApproval(Produce[Approval]):
    artifact_type = Approval

    async def produce(self, call):
        self.effects.create(
            Approval(parts=sorted(type(i.data).__name__ for i in call.inputs))
        )


class ApprovalGate(Agent):
    """A case `JoinConsume`/`AbsentConsume` alone can't express: require
    BOTH a Report and an answered PendingQuestion, AND forbid an existing
    HelpdeskTicket for the same thread — combining "join" and "absent" in
    one Consume."""

    consumes = [
        CorrelatedConsume(
            key=lambda d: d.thread_id,
            require=[Report, PendingQuestion],
            require_conditions={PendingQuestion: lambda d: d.answered},
            forbid=[HelpdeskTicket],
        )
    ]
    produces = [RecordApproval()]


def test_fires_when_required_present_and_forbidden_absent():
    ctx = Context()
    runtime = Runtime(ctx, agents=[ApprovalGate()])

    ctx.create(Report(thread_id="t1"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(Approval) == []  # question still missing

    ctx.create(PendingQuestion(thread_id="t1", answered=True))
    asyncio.run(runtime.arun())

    approvals = ctx.list_artifacts(Approval)
    assert len(approvals) == 1
    assert approvals[0].data.parts == ["PendingQuestion", "Report"]


def test_does_not_fire_when_an_existing_ticket_forbids_it():
    ctx = Context()
    runtime = Runtime(ctx, agents=[ApprovalGate()])

    ctx.create(HelpdeskTicket(thread_id="t1"))
    ctx.create(Report(thread_id="t1"))
    ctx.create(PendingQuestion(thread_id="t1", answered=True))
    asyncio.run(runtime.arun())

    assert ctx.list_artifacts(Approval) == []


def test_unanswered_question_does_not_complete_the_group_even_without_a_ticket():
    ctx = Context()
    runtime = Runtime(ctx, agents=[ApprovalGate()])

    ctx.create(Report(thread_id="t1"))
    ctx.create(PendingQuestion(thread_id="t1", answered=False))
    asyncio.run(runtime.arun())

    assert ctx.list_artifacts(Approval) == []
