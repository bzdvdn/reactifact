"""Approval steps: interpret an answer, clarify ambiguity, and the ready list."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...agent import Emit, Step
from ...artifacts import Artifact
from ...context import Context
from ...interaction import Human
from ...interrupt import PendingQuestion
from ...provenance import DERIVED_FROM
from ...providers import LLMProvider
from ...read import Read
from .classify import classify_approval
from .models import APPROVAL_KINDS, Approval, ApprovalDecision
from .policy import ApprovalPolicy


class InterpretApproval(Step):
    """Turn one answered approval question into an `ApprovalDecision`."""

    reads = (
        Read.idempotent(
            PendingQuestion,
            id_of=lambda question: f"approval:{question.id}",
            condition=lambda question: (
                question.data.answered and question.data.kind in APPROVAL_KINDS
            ),
        ),
    )
    produces = (ApprovalDecision,)

    def __init__(self, policy: ApprovalPolicy | None = None):
        super().__init__(artifact_type=ApprovalDecision)
        self.policy = policy or ApprovalPolicy()

    async def run(
        self,
        question: Artifact[PendingQuestion],
        llm: LLMProvider | None,
    ) -> Emit | None:
        data = question.data
        if data.kind not in APPROVAL_KINDS:
            return None
        decision_id = f"approval:{question.id}"
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

    reads = (ApprovalDecision,)
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
        # One clarification round only: a clarify-of-clarify must not loop, so
        # the guard is semantic, not just an idempotency check.
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

    The result is a plain step list, not a `Agent`: the runtime still
    derives scheduling from each step's `reads`. Pass `clarify=False` when
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

    def agent(
        self,
        name: str = "approval",
        *,
        consumes: Sequence[Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """A ready `Agent` around this approval flow.

        Defaults to waking on the answered `PendingQuestion` and the resulting
        `ApprovalDecision`; pass `consumes=` when the domain action reads more.
        """
        from ...agent import Agent
        from ...consume import Consume

        resolved = (
            list(consumes)
            if consumes is not None
            else [Consume(PendingQuestion), Consume(ApprovalDecision)]
        )
        return Agent(name, consumes=resolved, steps=list(self), **kwargs)


__all__ = ["ApprovalFlow", "ClarifyApproval", "InterpretApproval"]
