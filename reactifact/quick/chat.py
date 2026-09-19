"""The `quick.chat_agent` entry point: a configured `ChatAssistant`."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..agents import Agent
from ..budget import Budget
from ..chat import ChatAssistant
from ..checkpoints import FileKVBackend, InMemoryKVBackend
from ..context import Context
from ..providers import LLMProvider, from_env
from ..resources import RuntimeResources
from ..session import SessionStore
from .models import Question


def _as_store(store: SessionStore | str | Path | None) -> SessionStore:
    if store is None:
        return SessionStore(InMemoryKVBackend())
    if isinstance(store, SessionStore):
        return store
    return SessionStore(FileKVBackend(str(store)))


def _default_reply(
    user_message: type[BaseModel],
) -> Callable[[Context, str], dict[str, Any]]:
    def reply(ctx: Context, msg_id: str) -> dict[str, Any]:
        for artifact in sorted(
            ctx.list_artifacts(), key=lambda a: a.created_at, reverse=True
        ):
            if isinstance(artifact.data, user_message):
                continue
            text = getattr(artifact.data, "text", None)
            if isinstance(text, str) and text:
                return {"reply": text}
        return {"reply": ""}

    return reply


def chat_agent(
    agents: Sequence[Agent],
    *,
    store: SessionStore | str | Path | None = None,
    user_message: type[BaseModel] = Question,
    reply: Callable[[Context, str], dict[str, Any]] | None = None,
    llm: LLMProvider | None = None,
    resources: RuntimeResources | Callable[..., RuntimeResources] | None = None,
    budget: Budget | None = None,
    tracer: Any = None,
) -> ChatAssistant:
    """A configured `ChatAssistant` — sessions, turns, history, out of the box.

    `store` may be a `SessionStore`, a directory path (`FileKVBackend`), or
    `None` for a process-local in-memory store (handy for tests/notebooks; use a
    path for anything that must survive a restart). `reply` defaults to "the
    latest artifact with non-empty `text` that isn't the user's message" — pass
    your own for a richer payload.

    `llm=` is your provider (`from_env()`, `openai_llm(...)`, …) — same as the
    other `quick` entry points. It's only used when `resources=` is omitted:
    pass `resources=` instead to own the full `RuntimeResources` (extra sources,
    a per-turn callable, …). The provider instance is shared across turns, not
    closed by the assistant.
    """
    if resources is None:
        resources = RuntimeResources(llm=llm if llm is not None else from_env())
    return ChatAssistant(
        store=_as_store(store),
        agents=list(agents),
        user_message=user_message,
        reply=reply if reply is not None else _default_reply(user_message),
        resources=resources,
        budget=budget,
        tracer=tracer,
    )


__all__ = ["chat_agent"]
