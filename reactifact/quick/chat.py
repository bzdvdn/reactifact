"""The `quick.chat_agent` entry point: a configured `Chat` facade."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..agent import Agent, Step
from ..budget import Budget
from ..chat import Chat
from ..checkpoints import FileKVBackend, InMemoryKVBackend
from ..context import Context
from ..providers import LLMProvider, from_env
from ..resources import RuntimeResources
from ..runtime import Dispatcher, DispatchErrorHandler
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
    agents: Sequence[Agent] | None = None,
    *,
    groups: Sequence[Agent] | None = None,
    steps: Sequence[Step] | None = None,
    store: SessionStore | str | Path | None = None,
    user_message: type[BaseModel] = Question,
    reply: Callable[[Context, str], dict[str, Any]] | None = None,
    llm: LLMProvider | None = None,
    resources: RuntimeResources | Callable[..., RuntimeResources] | None = None,
    budget: Budget | None = None,
    tracer: Any = None,
    dispatcher: Dispatcher | None = None,
    on_dispatch_error: DispatchErrorHandler | None = None,
) -> Chat:
    """A configured `Chat` — sessions, turns, history, out of the box.

    `agents=` is the agent sequence; `groups=` is a compatibility alias.

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
    provided = [value for value in (agents, groups, steps) if value is not None]
    if len(provided) > 1:
        raise ValueError("Pass only one of steps=, agents= or groups=")
    resolved = agents if agents is not None else groups
    if resolved is None and steps is None:
        raise TypeError("chat_agent requires steps= or agents=")
    if resources is None:
        resources = RuntimeResources(llm=llm if llm is not None else from_env())
    return Chat(
        store=_as_store(store),
        steps=list(steps) if steps is not None else None,
        agents=list(resolved) if resolved is not None else None,
        seed_type=user_message,
        reply=reply if reply is not None else _default_reply(user_message),
        resources=resources,
        budget=budget,
        tracer=tracer,
        dispatcher=dispatcher,
        on_dispatch_error=on_dispatch_error,
    )


__all__ = ["chat_agent"]
