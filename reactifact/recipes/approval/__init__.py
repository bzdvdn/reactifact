"""Approval interpretation: turn a human answer into an explicit decision.

`PendingQuestion.approved()` stays deterministic. This recipe adds the free-text
layer: a typed yes/no or an explicit word is enough; an unclear answer goes to
one structured LLM call; anything conditional, uncertain or below the confidence
threshold becomes `ambiguous`/`conditional` and never auto-approves.

Customise one object:

    policy = ApprovalPolicy(
        instructions="'LGTM' means approve; 'hold' means reject.",
        examples=(("lgtm", "approve"), ("hold", "reject")),
    )
    intent = await classify_approval(llm, question, policy=policy)
"""

from __future__ import annotations

from .classify import classify_approval
from .models import (
    APPROVAL_KINDS,
    Approval,
    ApprovalChoice,
    ApprovalDecision,
    ApprovalIntent,
)
from .policy import APPROVAL_SYSTEM, APPROVAL_USER, ApprovalPolicy
from .steps import ApprovalFlow, ClarifyApproval, InterpretApproval

__all__ = [
    "APPROVAL_KINDS",
    "APPROVAL_SYSTEM",
    "APPROVAL_USER",
    "Approval",
    "ApprovalChoice",
    "ApprovalDecision",
    "ApprovalFlow",
    "ApprovalIntent",
    "ApprovalPolicy",
    "ClarifyApproval",
    "InterpretApproval",
    "classify_approval",
]
