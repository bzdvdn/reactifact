"""Approval models: the typed answer, the classifier result and the decision."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Existing apps use both spellings; new code should use "approval".
APPROVAL_KINDS = frozenset({"approval", "approve"})

ApprovalChoice = Literal["approve", "reject", "conditional", "ambiguous"]


class Approval(BaseModel):
    """Typed answer for a UI: two buttons instead of free text."""

    approved: bool


class ApprovalIntent(BaseModel):
    """The classifier's reading of a human answer."""

    decision: ApprovalChoice
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    conditions: list[str] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    """A durable, auditable interpretation of one `PendingQuestion`."""

    question_id: str
    decision: ApprovalChoice
    confidence: float
    reason: str = ""
    answer: str = ""
    conditions: list[str] = Field(default_factory=list)
    #: Hash of the policy/prompt that produced this decision (empty for typed
    #: answers and the deterministic parser).
    policy_hash: str = ""

    def allows(self, *, min_confidence: float = 0.8) -> bool:
        """Whether the action may proceed; ambiguity never does."""
        return self.decision == "approve" and self.confidence >= min_confidence


__all__ = [
    "APPROVAL_KINDS",
    "Approval",
    "ApprovalChoice",
    "ApprovalDecision",
    "ApprovalIntent",
]
