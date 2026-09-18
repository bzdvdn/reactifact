import asyncio

from pydantic import BaseModel
from reactifact import Agent, Context, Produce, Runtime
from reactifact.consume import AbsentConsume


class Report(BaseModel):
    thread_id: str


class HelpdeskTicket(BaseModel):
    thread_id: str
    sent: bool = False


class Filed(BaseModel):
    thread_id: str


class FileTicket(Produce[Filed]):
    artifact_type = Filed

    async def produce(self, call):
        for i in call.inputs:
            self.effects.create(Filed(thread_id=i.data.thread_id))


class TicketGate(Agent):
    consumes = [
        AbsentConsume(Report, absent_type=HelpdeskTicket, key=lambda d: d.thread_id)
    ]
    produces = [FileTicket()]


def test_fires_when_no_blocking_ticket_exists():
    ctx = Context()
    runtime = Runtime(ctx, agents=[TicketGate()])

    ctx.create(Report(thread_id="t1"))
    asyncio.run(runtime.arun())

    filed = ctx.list_artifacts(Filed)
    assert len(filed) == 1
    assert filed[0].data.thread_id == "t1"


def test_does_not_fire_when_a_ticket_already_exists_for_the_same_key():
    ctx = Context()
    runtime = Runtime(ctx, agents=[TicketGate()])

    ctx.create(HelpdeskTicket(thread_id="t1"))
    ctx.create(Report(thread_id="t1"))
    asyncio.run(runtime.arun())

    assert ctx.list_artifacts(Filed) == []


def test_unrelated_ticket_key_does_not_block():
    ctx = Context()
    runtime = Runtime(ctx, agents=[TicketGate()])

    ctx.create(HelpdeskTicket(thread_id="other-thread"))
    ctx.create(Report(thread_id="t1"))
    asyncio.run(runtime.arun())

    filed = ctx.list_artifacts(Filed)
    assert len(filed) == 1
    assert filed[0].data.thread_id == "t1"


def test_absent_condition_narrows_what_counts_as_blocking():
    """A drafted-but-unsent ticket doesn't block; only a `sent` one does."""

    class SentOnlyGate(Agent):
        consumes = [
            AbsentConsume(
                Report,
                absent_type=HelpdeskTicket,
                key=lambda d: d.thread_id,
                absent_condition=lambda d: d.sent,
            )
        ]
        produces = [FileTicket()]

    ctx = Context()
    runtime = Runtime(ctx, agents=[SentOnlyGate()])

    ctx.create(HelpdeskTicket(thread_id="t1", sent=False))
    ctx.create(Report(thread_id="t1"))
    asyncio.run(runtime.arun())
    assert len(ctx.list_artifacts(Filed)) == 1  # unsent ticket didn't block

    ctx.create(HelpdeskTicket(thread_id="t2", sent=True))
    ctx.create(Report(thread_id="t2"))
    asyncio.run(runtime.arun())
    assert [f.data.thread_id for f in ctx.list_artifacts(Filed)] == ["t1"]  # t2 blocked


def test_collect_excludes_blocked_reports_even_when_the_agent_also_fires_for_others():
    """One Report in the batch is blocked, another isn't — only the unblocked
    one should ever reach `produce()` as an input."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[TicketGate()])

    ctx.create(HelpdeskTicket(thread_id="blocked"))
    ctx.create(Report(thread_id="blocked"))
    ctx.create(Report(thread_id="open"))
    asyncio.run(runtime.arun())

    assert [f.data.thread_id for f in ctx.list_artifacts(Filed)] == ["open"]
