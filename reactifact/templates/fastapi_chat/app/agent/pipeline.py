"""ChatAssistant assembly: hooks, agents and settings-driven wiring."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from reactifact import Agent, Budget, SessionStore
from reactifact.chat import ChatAssistant, ChatMemory
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


class AnswerAgent(Agent):
    name = "answer"
    steps = [AnswerStep()]


def create_message(ctx: Any, text: str, session_id: str) -> str:
    return str(ctx.create(UserQuery(text=text, session_id=session_id)).id)


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
    agents: Sequence[Agent],
    budget: Budget,
    memory: ChatMemory | None,
) -> ChatAssistant:
    return ChatAssistant(
        store=store,
        agents=agents,
        user_message=UserQuery,
        create_message=create_message,
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
    "create_message",
    "make_reply",
    "session_state",
]
