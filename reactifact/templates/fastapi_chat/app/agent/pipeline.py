"""Chat assembly: typed facade hooks and settings-driven wiring."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from reactifact import Budget, Chat, SessionStore, StepGroup
from reactifact.chat import ChatMemory
from reactifact.recipes.conversation import MessageSpec, Transcript
from reactifact.tracing import Tracer, TraceStore

from app.agent.resources import build_resources
from app.agent.steps import AnswerStep
from app.config import Settings
from app.models import Answer, UserQuery

_TRANSCRIPT = Transcript(
    [
        MessageSpec(UserQuery, "user"),
        MessageSpec(Answer, "assistant"),
    ]
)


class AnswerAgent(StepGroup):
    name = "answer"
    steps = [AnswerStep()]


def session_state(ctx: Any) -> dict[str, Any]:
    return _TRANSCRIPT.state(ctx)


def make_reply(fallback: str) -> Callable[[Any, str], dict[str, Any]]:
    def reply(ctx: Any, message_id: str) -> dict[str, Any]:
        answers = [a for a in ctx.list_artifacts(Answer) if a.data.query_id == message_id]
        answer = max(answers, key=lambda a: a.created_at) if answers else None
        return {
            "reply": answer.data.text if answer is not None else fallback,
            "waiting": False,
        }

    return reply


def build_assistant(
    *,
    settings: Settings,
    store: SessionStore,
    trace_store: TraceStore,
    groups: Sequence[StepGroup],
    budget: Budget,
    memory: ChatMemory | None,
) -> Chat:
    return Chat(
        store=store,
        groups=groups,
        seed_type=UserQuery,
        answer_type=Answer,
        reply=make_reply(settings.fallback_reply),
        session_state=session_state,
        resources=lambda session_id, request: build_resources(settings, request),
        budget=budget,
        max_concurrency=settings.chat_max_concurrency,
        memory=memory,
        session_save_policy=settings.chat_session_save_policy,
        fallback_reply=settings.fallback_reply,
        tracer=((lambda: Tracer(store=trace_store)) if settings.tracing_enabled else None),
    )


__all__ = [
    "AnswerAgent",
    "build_assistant",
    "make_reply",
    "session_state",
]
