"""HITL questions and outbox actions stored in the Context (§42, §60)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..artifacts import Artifact
from ..interrupt import PendingAction, PendingQuestion

if TYPE_CHECKING:
    from .context import Context


def interrupt(
    context: Context,
    question: str,
    kind: str = "general",
    notes: dict[str, object] | None = None,
) -> Artifact[PendingQuestion]:
    return context.create(
        PendingQuestion(question=question, kind=kind, notes=notes or {})
    )


def pending_questions(context: Context) -> list[Artifact[PendingQuestion]]:
    return [
        artifact
        for artifact in context.list_artifacts(PendingQuestion)
        if not artifact.data.answered
    ]


def has_pending_question(context: Context) -> bool:
    return bool(pending_questions(context))


def latest_pending_question(
    context: Context,
) -> Artifact[PendingQuestion] | None:
    questions = pending_questions(context)
    if not questions:
        return None
    return max(questions, key=lambda artifact: artifact.created_at)


def resume(
    context: Context, question_id: str, answer: str
) -> Artifact[PendingQuestion] | None:
    artifact = context.get(question_id)
    if artifact is None or not isinstance(artifact.data, PendingQuestion):
        return None
    updated = artifact.data.model_copy(
        update={
            "answered": True,
            "resolution": answer,
            "resolved_at": datetime.now(UTC),
        }
    )
    return context.update(question_id, updated)


def pending_actions(context: Context) -> list[Artifact[PendingAction]]:
    return [
        artifact
        for artifact in context.list_artifacts(PendingAction)
        if artifact.data.status == "pending"
    ]


def mark_dispatched(context: Context, action_id: str) -> Artifact[PendingAction] | None:
    artifact = context.get(action_id)
    if artifact is None or not isinstance(artifact.data, PendingAction):
        return None
    updated = artifact.data.model_copy(
        update={
            "status": "dispatched",
            "dispatched_at": datetime.now(UTC),
            "error": None,
        }
    )
    return context.update(action_id, updated)


def mark_failed(
    context: Context, action_id: str, error: str | None = None
) -> Artifact[PendingAction] | None:
    artifact = context.get(action_id)
    if artifact is None or not isinstance(artifact.data, PendingAction):
        return None
    updated = artifact.data.model_copy(
        update={
            "status": "failed",
            "error": error,
            "attempts": artifact.data.attempts + 1,
        }
    )
    return context.update(action_id, updated)


def retry_action(context: Context, action_id: str) -> Artifact[PendingAction] | None:
    artifact = context.get(action_id)
    if artifact is None or not isinstance(artifact.data, PendingAction):
        return None
    updated = artifact.data.model_copy(
        update={"status": "pending", "error": None, "attempts": 0}
    )
    return context.update(action_id, updated)


__all__ = [
    "has_pending_question",
    "interrupt",
    "latest_pending_question",
    "mark_dispatched",
    "mark_failed",
    "pending_actions",
    "pending_questions",
    "resume",
    "retry_action",
]
