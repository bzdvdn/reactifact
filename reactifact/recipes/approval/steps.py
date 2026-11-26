"""Approval steps: interpret an answer, clarify ambiguity, and the ready list."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...artifacts import Artifact
from ...consume import Consume
from ...context import Context
from ...interaction import Human
from ...interrupt import PendingQuestion
from ...provenance import DERIVED_FROM
from ...providers import LLMProvider
from ...step import Emit, Step
from .classify import classify_approval
from .models import APPROVAL_KINDS, Approval, ApprovalDecision
from .policy import ApprovalPolicy


class InterpretApproval(Step):
    """Turn one answered approval question into an `ApprovalDecision`."""

    consumes = (Consume.by_field(PendingQuestion, "answered", True),)
    produces = (ApprovalDecision,)

    def __init__(self, policy: ApprovalPolicy | None = None):
        super().__init__(artifact_type=ApprovalDecision)
        self.policy = policy or ApprovalPolicy()

    async def run(
        self,
        context: Context,
        question: Artifact[PendingQuestion],
        llm: LLMProvider | None,
    ) -> Emit | None:
        data = question.data
        if data.kind not in APPROVAL_KINDS:
            return None
        decision_id = f"approval:{question.id}"
        if context.get(decision_id) is not None:
            return None
        intent = await classify_approval(llm, data, policy=self.policy)
        return Emit(
            ApprovalDecision(
                question_id=question.id,
                answer=data.answer_text(),
                policy_hash=self.policy.policy_hash,
                **intent.model_dump(),
            ),
            id=decision_id,
            links=((DERIVED_FROM, question),),
        )


class ClarifyApproval(Step):
    """Ask a typed follow-up when an answer is ambiguous or conditional.

    The follow-up uses `Approval` (`{"approved": bool}`), so a UI can render two
    buttons. One round only; a still-ambiguous answer stays ambiguous.
    """

    consumes = (ApprovalDecision,)
    produces = (PendingQuestion,)
    question_text = (
        "I couldn't read that as a clear approval. "
        "Please confirm explicitly: approve or reject?"
    )

    async def run(
        self,
        context: Context,
        decision: ApprovalDecision,
        human: Human,
    ) -> None:
        if decision.decision not in {"ambiguous", "conditional"}:
            return None
        if decision.question_id.startswith("clarify:"):
            return None  # one clarification round, no loop
        follow_id = f"clarify:{decision.question_id}"
        if context.get(follow_id) is not None:
            return None
        human.ask(
            self.question_text,
            kind="approval",
            schema=Approval,
            id=follow_id,
            notes={"approval_of": decision.question_id},
        )
        return None


class ApprovalFlow(list[Step[Any]]):
    """The approval machinery as a ready step list.

    `ApprovalFlow` hides `InterpretApproval` and `ClarifyApproval`:

        flow = ApprovalFlow(
            policy=policy,
            steps=[DeployOnApproval()],   # your domain action
        )
        pipeline = Pipeline(steps=[AskApproval(), *flow])

    The result is a plain step list, not a `StepGroup`: the runtime still
    derives scheduling from each step's `consumes`. Pass `clarify=False` when
    the app handles ambiguous answers itself.
    """

    def __init__(
        self,
        *,
        policy: ApprovalPolicy | None = None,
        steps: Sequence[Step[Any]] = (),
        clarify: bool = True,
    ):
        built: list[Step[Any]] = [InterpretApproval(policy=policy)]
        if clarify:
            built.append(ClarifyApproval())
        built.extend(steps)
        super().__init__(built)


__all__ = ["ApprovalFlow", "ClarifyApproval", "InterpretApproval"]
