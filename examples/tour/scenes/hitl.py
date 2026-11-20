"""Typed HITL: a question with a JSON Schema, resumed with a Pydantic model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    Effects,
    PendingQuestion,
    Runtime,
    Step,
    create_agent,
)


class RefundRequest(BaseModel):
    order_id: str
    amount: int


class Approval(BaseModel):
    approved: bool
    note: str = ""


class Decision(BaseModel):
    text: str


class AskApproval(Step):
    consumes = (RefundRequest,)
    produces = (PendingQuestion,)

    async def run(self, request: RefundRequest, effects: Effects) -> None:
        effects.ask(
            f"Approve refund of {request.amount} for {request.order_id}?",
            kind="approve",
            schema=Approval,
            notes={"order_id": request.order_id},
            id="approval:refund",
        )


class Publish(Step):
    consumes = (Consume.by_field(PendingQuestion, "answered", True),)
    produces = (Decision,)

    async def run(self, question: PendingQuestion) -> Decision:
        state = "approved" if question.approved() else "rejected"
        note = (
            question.resolution_data.get("note", "") if question.resolution_data else ""
        )
        return Decision(text=f"{state}: {note}".strip())


async def run_hitl() -> dict[str, Any]:
    context = Context()
    context.create(RefundRequest(order_id="A-17", amount=4200), id="refund:1")
    runtime = Runtime(
        context,
        agents=[create_agent("refunds", steps=[AskApproval(), Publish()])],
    )

    await runtime.arun()
    question = context.latest_pending_question()
    assert question is not None

    # A human (or a Studio form) answers with the typed model.
    context.resume(question.id, Approval(approved=True, note="within policy"))
    await runtime.arun()

    final = context.latest(Decision)
    updated = context.get(question.id)
    return {
        "schema_present": bool(question.data.resolution_schema),
        "answered": updated.data.answered if updated is not None else False,
        "approved": updated.data.approved() if updated is not None else False,
        "note": (
            updated.data.resolution_data.get("note", "")
            if updated is not None and updated.data.resolution_data
            else ""
        ),
        "final": final.data.text if final is not None else None,
    }
