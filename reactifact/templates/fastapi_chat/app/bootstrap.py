"""Composition of settings into stores, budget, memory and the assistant."""

from __future__ import annotations

from reactifact import Budget, Chat, Agent, SessionStore
from reactifact.chat import ChatMemory
from reactifact.checkpoints import (
    FileKVBackend,
    InMemoryKVBackend,
    PostgreSQLKVBackend,
    SQLiteKVBackend,
)
from reactifact.tracing import TraceStore

from app.agent.pipeline import build_answer_agent, build_assistant
from app.config import Settings
from app.models import Answer, UserQuery


def build_session_store(settings: Settings) -> SessionStore:
    backend: str = settings.session_backend
    if backend == "memory":
        return SessionStore(InMemoryKVBackend())
    if backend == "file":
        return SessionStore(FileKVBackend(settings.session_path))
    if backend == "sqlite":
        return SessionStore(SQLiteKVBackend(settings.session_path))
    if backend == "postgres":
        if not settings.session_url:
            raise ValueError("APP_SESSION_URL is required for postgres sessions")
        return SessionStore(PostgreSQLKVBackend(settings.session_url))
    raise ValueError(f"unknown session_backend={backend!r}")


def build_trace_store(settings: Settings) -> TraceStore:
    return TraceStore(settings.trace_path)


def build_budget(settings: Settings) -> Budget:
    return Budget(
        max_runs=settings.budget_max_runs,
        max_iterations=settings.budget_max_iterations,
        max_seconds=settings.budget_max_seconds,
        max_tool_calls=settings.budget_max_tool_calls,
        max_tokens=settings.budget_max_tokens,
        max_cost=settings.budget_max_cost,
    )


def build_chat_memory(settings: Settings) -> ChatMemory | None:
    if settings.chat_memory_keep is None and settings.chat_memory_commits is None:
        return None
    if settings.chat_memory_keep is None:
        return ChatMemory(compact_commits=settings.chat_memory_commits)
    return ChatMemory(
        message_types=[UserQuery, Answer],
        keep=settings.chat_memory_keep,
        compact_commits=settings.chat_memory_commits,
    )


def build_agents(settings: Settings) -> list[Agent]:
    return [build_answer_agent(timeout=settings.agent_timeout_seconds)]


def build_assistant_from_settings(
    settings: Settings,
    store: SessionStore,
    trace_store: TraceStore,
) -> Chat:
    return build_assistant(
        settings=settings,
        store=store,
        trace_store=trace_store,
        agents=build_agents(settings),
        budget=build_budget(settings),
        memory=build_chat_memory(settings),
    )


__all__ = [
    "build_agents",
    "build_assistant_from_settings",
    "build_budget",
    "build_chat_memory",
    "build_session_store",
    "build_trace_store",
]
