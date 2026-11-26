"""Approval policy: the prompt, confidence threshold and deterministic vocabulary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from ...interrupt import PendingQuestion
from .models import ApprovalChoice

APPROVAL_SYSTEM = """\
You classify a human answer to an approval question.
Return exactly one decision:
- approve: the person clearly says yes / approves;
- reject: the person clearly says no / rejects;
- conditional: yes or no depends on a condition ("if tests pass", "after review");
- ambiguous: the answer is unclear, sarcastic, negated, or otherwise unsafe to read.
Do not decide whether the action itself is safe. Prefer conditional/ambiguous
over approve when the text contains a condition or doubt. Never invent consent.
"""

APPROVAL_USER = """\
Question: {question}
Kind: {kind}
Notes: {notes}
Human answer: {answer}

Classify the human answer. Return JSON matching the ApprovalIntent schema.
{examples}Additional instructions: {instructions}
"""


@dataclass(frozen=True)
class ApprovalPolicy:
    """Prompt, confidence and vocabulary for one approval domain.

    `system`/`instructions`/`examples` shape the LLM prompt; the phrase tuples
    make obvious answers deterministic (no model call). `prompt_builder`, when
    set, replaces the default user prompt entirely.
    """

    threshold: float = 0.8
    system: str = APPROVAL_SYSTEM
    instructions: str = ""
    examples: tuple[tuple[str, ApprovalChoice], ...] = ()
    approve_phrases: tuple[str, ...] = (
        "go ahead",
        "ship it",
        "do it",
        "sounds good",
        "approved",
        "approve",
        "yes",
        "давай",
        "запускай",
        "одобряю",
        "согласен",
        "согласна",
        "ок",
    )
    reject_phrases: tuple[str, ...] = (
        "not approved",
        "not approve",
        "do not",
        "don't",
        "no way",
        "not yet",
        "не надо",
        "не запускай",
        "не одобряю",
        "не согласен",
        "не согласна",
        "не сейчас",
        "нельзя",
        "отмена",
    )
    conditional_markers: tuple[str, ...] = (
        "if",
        "unless",
        "only if",
        "provided",
        "assuming",
        "after",
        "once",
        "when",
        "если",
        "при условии",
        "после",
        "когда",
        "только если",
    )
    ambiguous_markers: tuple[str, ...] = (
        "maybe",
        "perhaps",
        "possibly",
        "not sure",
        "unsure",
        "i think",
        "not certain",
        "наверное",
        "возможно",
        "не уверен",
        "не уверена",
        "не знаю",
        "может быть",
        "хм",
    )
    prompt_builder: Callable[[PendingQuestion], tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")

    @property
    def policy_hash(self) -> str:
        """Stable fingerprint of the prompt + vocabulary (not the callback)."""
        payload = {
            "threshold": self.threshold,
            "system": self.system,
            "instructions": self.instructions,
            "examples": self.examples,
            "approve": self.approve_phrases,
            "reject": self.reject_phrases,
            "conditional": self.conditional_markers,
            "ambiguous": self.ambiguous_markers,
            "prompt_builder": getattr(
                self.prompt_builder, "__qualname__", type(self.prompt_builder).__name__
            ),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

    def prompt(self, question: PendingQuestion) -> tuple[str, str]:
        """The `(system, user)` pair for the LLM fallback."""
        if self.prompt_builder is not None:
            return self.prompt_builder(question)
        examples = ""
        if self.examples:
            lines = "\n".join(
                f'- "{text}" -> {decision}' for text, decision in self.examples
            )
            examples = f"Examples:\n{lines}\n\n"
        user = APPROVAL_USER.format(
            question=question.question,
            kind=question.kind,
            notes=json.dumps(question.notes, ensure_ascii=False, default=str),
            answer=question.answer_text(),
            examples=examples,
            instructions=self.instructions,
        )
        return self.system, user


__all__ = ["APPROVAL_SYSTEM", "APPROVAL_USER", "ApprovalPolicy"]
