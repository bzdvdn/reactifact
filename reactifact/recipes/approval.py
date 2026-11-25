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
    intent = await classify_approval(context, question, policy=policy)
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..artifacts import Artifact
from ..consume import Consume
from ..context import Context
from ..effects import Effects
from ..interaction import Human
from ..interrupt import PendingQuestion
from ..provenance import DERIVED_FROM
from ..step import Step
from ..structured import structured_llm

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
    context: Context,
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

    system, user = policy.prompt(question)
    intent = await structured_llm(
        context,
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
        effects: Effects,
    ) -> None:
        data = question.data
        if data.kind not in APPROVAL_KINDS:
            return None
        decision_id = f"approval:{question.id}"
        if context.get(decision_id) is not None:
            return None
        intent = await classify_approval(context, data, policy=self.policy)
        handle = effects.create(
            ApprovalDecision(
                question_id=question.id,
                answer=data.answer_text(),
                policy_hash=self.policy.policy_hash,
                **intent.model_dump(),
            ),
            id=decision_id,
        )
        handle.link(DERIVED_FROM, question)
        return None


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


__all__ = [
    "APPROVAL_KINDS",
    "APPROVAL_SYSTEM",
    "APPROVAL_USER",
    "Approval",
    "ApprovalChoice",
    "ApprovalFlow",
    "ApprovalDecision",
    "ApprovalIntent",
    "ApprovalPolicy",
    "ClarifyApproval",
    "InterpretApproval",
    "classify_approval",
]
