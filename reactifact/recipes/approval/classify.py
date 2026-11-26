"""Approval classification: typed/deterministic first, one LLM fallback."""

from __future__ import annotations

import re

from ...interrupt import PendingQuestion
from ...providers import LLMProvider
from ...structured import structured_llm
from .models import APPROVAL_KINDS, ApprovalIntent
from .policy import ApprovalPolicy


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase = phrase.strip()
    if not phrase:
        return False
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _explicit_intent(
    question: PendingQuestion, policy: ApprovalPolicy
) -> ApprovalIntent | None:
    """Typed answer or unambiguous natural-language yes/no; no LLM."""
    data = question.resolution_data
    if isinstance(data, dict) and isinstance(data.get("approved"), bool):
        return ApprovalIntent(
            decision="approve" if data["approved"] else "reject",
            confidence=1.0,
            reason="typed approval answer",
        )

    text = question.answer_text().strip()
    if not text:
        return None
    raw = text.casefold()
    if any(_contains_phrase(raw, marker) for marker in policy.conditional_markers):
        return ApprovalIntent(
            decision="conditional",
            confidence=0.9,
            reason="the answer contains a condition",
            conditions=[text],
        )
    if any(_contains_phrase(raw, marker) for marker in policy.ambiguous_markers):
        return ApprovalIntent(
            decision="ambiguous",
            confidence=0.2,
            reason="the answer signals uncertainty",
        )
    if any(_contains_phrase(raw, phrase) for phrase in policy.reject_phrases):
        return ApprovalIntent(
            decision="reject",
            confidence=0.95,
            reason="explicit rejection",
        )
    if any(_contains_phrase(raw, phrase) for phrase in policy.approve_phrases):
        return ApprovalIntent(
            decision="approve",
            confidence=0.95,
            reason="explicit approval",
        )

    words = set(re.findall(r"[a-zа-яё0-9']+", raw))
    approve = bool(words & {"yes", "y", "true", "approve", "approved", "да", "ок"})
    reject = bool(words & {"no", "n", "reject", "rejected", "false", "нет"})
    if approve and reject:
        return ApprovalIntent(
            decision="ambiguous",
            confidence=0.2,
            reason="the answer contains both approval and rejection words",
        )
    if reject:
        return ApprovalIntent(
            decision="reject",
            confidence=0.95,
            reason="explicit rejection word",
        )
    if approve:
        return ApprovalIntent(
            decision="approve",
            confidence=0.95,
            reason="explicit approval word",
        )
    return None


async def classify_approval(
    llm: LLMProvider | None,
    question: PendingQuestion,
    *,
    policy: ApprovalPolicy | None = None,
) -> ApprovalIntent:
    """Classify one answered approval question.

    Typed answers and clear yes/no text never reach the LLM. Anything else uses
    `structured_llm`; a missing model, low confidence or a conditional answer
    becomes `ambiguous`/`conditional`, never `approve`.
    """
    policy = policy or ApprovalPolicy()
    if not question.answered:
        return ApprovalIntent(
            decision="ambiguous", confidence=0.0, reason="question is not answered"
        )
    if question.kind not in APPROVAL_KINDS:
        return ApprovalIntent(
            decision="ambiguous",
            confidence=0.0,
            reason=f"question kind {question.kind!r} is not an approval",
        )

    explicit = _explicit_intent(question, policy)
    if explicit is not None:
        return explicit

    if llm is None:
        return ApprovalIntent(
            decision="ambiguous",
            confidence=0.0,
            reason="approval classifier needs an LLMProvider",
        )
    system, user = policy.prompt(question)
    intent = await structured_llm(
        llm,
        schema=ApprovalIntent,
        system=system,
        user=user,
        prompt_hash=policy.policy_hash,
        temperature=0.0,
    )
    if intent is None:
        return ApprovalIntent(
            decision="ambiguous",
            confidence=0.0,
            reason="approval classifier is unavailable or returned invalid JSON",
        )
    if intent.conditions or any(
        _contains_phrase(question.answer_text().casefold(), marker)
        for marker in policy.conditional_markers
    ):
        return ApprovalIntent(
            decision="conditional",
            confidence=min(intent.confidence, 0.9),
            reason=intent.reason or "the answer contains a condition",
            conditions=intent.conditions or [question.answer_text()],
        )
    if (
        intent.decision in {"approve", "reject"}
        and intent.confidence < policy.threshold
    ):
        return ApprovalIntent(
            decision="ambiguous",
            confidence=intent.confidence,
            reason="classifier confidence is below the approval threshold",
        )
    return intent


__all__ = ["classify_approval"]
