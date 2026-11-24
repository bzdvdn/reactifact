"""Human/Outbox collaborators keep Effects as a pure write surface."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Human, Outbox, PendingAction, PendingQuestion, Pipeline, step


class Ticket(BaseModel):
    id: str
    text: str


class Approval(BaseModel):
    approved: bool


@step(produces=PendingQuestion, consumes=Ticket)
def approve(ticket: Ticket, human: Human) -> None:
    human.ask("Approve?", schema=Approval, id=f"approve:{ticket.id}")


@step(produces=PendingAction, consumes=Ticket)
def notify(ticket: Ticket, outbox: Outbox) -> None:
    outbox.act("notify", key=f"notify:{ticket.id}", payload={"id": ticket.id})


@step(produces=PendingQuestion, consumes=Ticket)
async def approve_call(call) -> None:
    call.human.ask("Approve?", schema=Approval, id=f"call:{call.trigger.id}")


def test_human_creates_a_pending_question():
    result = Pipeline(steps=[approve]).run(Ticket(id="42", text="refund"))
    questions = result.context.pending_questions()
    assert len(questions) == 1
    assert questions[0].data.resolution_schema


def test_outbox_records_a_pending_action():
    result = Pipeline(steps=[notify]).run(Ticket(id="7", text="x"))
    actions = result.context.pending_actions()
    assert len(actions) == 1
    assert actions[0].data.idempotency_key == "notify:7"


def test_stepcall_exposes_human():
    result = Pipeline(steps=[approve_call]).run(Ticket(id="9", text="x"))
    assert len(result.context.pending_questions()) == 1
