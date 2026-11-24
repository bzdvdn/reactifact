"""Swarm: a peer-to-peer handoff with provenance and a hop limit."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from reactifact import Context, Effects, Step, create_step_group
from reactifact.recipes.swarm import Handoff, swarm


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class TriageStep(Step):
    consumes = (Question,)
    produces = (Handoff,)

    async def run(self, question: Question, effects: Effects) -> None:
        effects.create(
            Handoff(
                to="billing",
                reason="refund request",
                payload={"text": question.text},
            ),
            id="handoff:triage",
        )


class BillingStep(Step):
    consumes = (Handoff,)
    produces = (Answer,)

    async def run(self, handoff: Handoff) -> Answer:
        return Answer(text=f"billing handled: {handoff.payload['text']}")


async def run_swarm_scene() -> dict[str, Any]:
    result = await swarm(
        Context(),
        groups={
            "triage": create_step_group("triage", steps=[TriageStep()]),
            "billing": create_step_group("billing", steps=[BillingStep()]),
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
