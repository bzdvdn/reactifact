import asyncio

from pydantic import BaseModel
from reactifact import Agent, Context, Produce, Runtime
from reactifact.consume import JoinConsume


class Report(BaseModel):
    thread_id: str
    summary: str


class PendingQuestion(BaseModel):
    thread_id: str
    answered: bool = False


class Approval(BaseModel):
    parts: list[str]


class RecordApproval(Produce[Approval]):
    artifact_type = Approval

    async def produce(self, call):
        self.effects.create(
            Approval(parts=sorted(type(i.data).__name__ for i in call.inputs))
        )


class ApprovalGate(Agent):
    consumes = [
        JoinConsume(
            Report,
            PendingQuestion,
            key=lambda d: d.thread_id,
            part_conditions={PendingQuestion: lambda d: d.answered},
        )
    ]
    produces = [RecordApproval()]


def test_neither_part_alone_fires_the_join():
    ctx = Context()
    runtime = Runtime(ctx, agents=[ApprovalGate()])

    ctx.create(Report(thread_id="t1", summary="s"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(Approval) == []

    ctx.create(PendingQuestion(thread_id="t2", answered=True))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(Approval) == []


def test_join_fires_when_report_arrives_after_the_answered_question():
    """Proves the join wakes up on *either* part's event, not just the one
    that happened to complete the pair in the other order (see the test
    below for report-first)."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[ApprovalGate()])

    ctx.create(PendingQuestion(thread_id="t1", answered=True))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(Approval) == []  # Report still missing

    ctx.create(Report(thread_id="t1", summary="s"))
    asyncio.run(runtime.arun())

    approvals = ctx.list_artifacts(Approval)
    assert len(approvals) == 1
    assert approvals[0].data.parts == ["PendingQuestion", "Report"]


def test_join_fires_when_the_answer_arrives_after_the_report():
    ctx = Context()
    runtime = Runtime(ctx, agents=[ApprovalGate()])

    ctx.create(Report(thread_id="t1", summary="s"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(Approval) == []  # question still missing

    question = ctx.create(PendingQuestion(thread_id="t1", answered=False))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(Approval) == []  # unanswered — part_conditions rejects it

    ctx.update(question.id, PendingQuestion(thread_id="t1", answered=True))
    asyncio.run(runtime.arun())

    approvals = ctx.list_artifacts(Approval)
    assert len(approvals) == 1
    assert approvals[0].data.parts == ["PendingQuestion", "Report"]


def test_mismatched_key_never_joins():
    ctx = Context()
    runtime = Runtime(ctx, agents=[ApprovalGate()])

    ctx.create(Report(thread_id="t1", summary="s"))
    asyncio.run(runtime.arun())
    ctx.create(PendingQuestion(thread_id="t2", answered=True))
    asyncio.run(runtime.arun())

    assert ctx.list_artifacts(Approval) == []


def test_collect_returns_only_the_complete_groups_parts():
    """A second, incomplete group (t2) must not leak into the inputs of the
    run triggered by the completed one (t1)."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[ApprovalGate()])

    ctx.create(Report(thread_id="t2", summary="incomplete"))  # never gets a question
    ctx.create(Report(thread_id="t1", summary="s"))
    asyncio.run(runtime.arun())
    ctx.create(PendingQuestion(thread_id="t1", answered=True))
    asyncio.run(runtime.arun())

    approvals = ctx.list_artifacts(Approval)
    assert len(approvals) == 1
    assert approvals[0].data.parts == ["PendingQuestion", "Report"]
