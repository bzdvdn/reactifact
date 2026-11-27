"""An approval gate in one chat: ask, interpret, and only then deploy.

Run it offline:

    python -m examples.approval_gate

The reusable part lives in `reactifact.recipes` (`InterpretApproval`,
`ClarifyApproval`, `ApprovalDecision`). `DeployOnApproval` below is deliberately
domain code: it decides what "approve" means for this app.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel
from reactifact import Agent, Chat, Human, PendingQuestion, Read, Step
from reactifact.recipes import ApprovalDecision, ApprovalFlow


class Request(BaseModel):
    text: str
    session_id: str = ""


class Deployment(BaseModel):
    text: str


class AskApproval(Step):
    """Ask a human to confirm the request."""

    reads = (Request,)
    produces = (PendingQuestion,)

    async def run(self, request: Request, human: Human) -> None:
        human.ask(
            f"Deploy {request.text!r} to production?",
            kind="approval",
            notes={"request": request.text},
        )
        return None


class DeployOnApproval(Step):
    """Domain example: only an explicit `approve` runs the action."""

    # Wait for the decision; Request is only a read-only input.
    reads = (Read(ApprovalDecision),)
    produces = (Deployment,)

    async def run(
        self, decision: ApprovalDecision, request: Request
    ) -> Deployment | None:
        if not decision.allows():
            return None
        return Deployment(text=f"deployed {request.text!r}")


#: Ask for approval, then let `ApprovalFlow` interpret the answer.
APPROVAL = Agent(
    "approval",
    steps=[
        AskApproval(),  # create the PendingQuestion
        *ApprovalFlow(
            steps=[DeployOnApproval()],  # domain action
        ),
    ],
)


def build_chat() -> Chat:
    return Chat(
        agents=[APPROVAL],
        seed_type=Request,
        answer_type=Deployment,
        resume_pending=True,
    )


async def main() -> dict[str, Any]:
    chat = build_chat()
    first = await chat.invoke("checkout", session_id="demo")
    second = await chat.invoke("yes, ship it", session_id="demo")
    return {"waiting": first, "answer": second}


if __name__ == "__main__":
    print(asyncio.run(main()))
