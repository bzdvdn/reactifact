"""Swarm: a peer-to-peer handoff with provenance and a hop limit."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Emit, Step
from reactifact.recipes.swarm import Handoff, swarm


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class TriageStep(Step):
    reads = (Question,)
    produces = (Handoff,)

    async def run(self, question: Question) -> Emit:
        return Emit(
            Handoff(
                to="billing",
                reason="refund request",
                payload={"text": question.text},
            ),
            id="handoff:triage",
        )


class BillingStep(Step):
    reads = (Handoff,)
    produces = (Answer,)

    async def run(self, handoff: Handoff) -> Answer:
        return Answer(text=f"billing handled: {handoff.payload['text']}")


async def run_swarm_scene() -> dict[str, Any]:
    result = await swarm(
        Context(),
        agents={
            "triage": Agent(
                "triage", consumes=[Consume(Question)], steps=[TriageStep()]
            ),
            "billing": Agent(
                "billing", consumes=[Consume(Handoff)], steps=[BillingStep()]
            ),
        },
        start="triage",
        input=Question(text="refund please"),
        final_type=Answer,
        max_hops=4,
    )
    handoff = result.handoffs[0].data if result.handoffs else None
    return {
        "final": result.final.data.text if result.final is not None else None,
        "hops": len(result.handoffs),
        "from": handoff.from_agent if handoff is not None else None,
        "to": handoff.to if handoff is not None else None,
        "run_ids": len(result.run_ids),
        "relations": len(result.context.relations()),
    }
