"""FastAPI apps exposing the example agents over A2A.

`create_a2a_router(agents)` adds the Agent Card (`/.well-known/agent-card.json`)
and the JSON-RPC endpoint (`POST /`) to any FastAPI app. Building one app per
agent keeps the example focused — a real deployment can register several agents
on one app (its card lists each agent's `produces` as a skill).
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI
from reactifact.a2a import create_a2a_router
from reactifact.agents import Agent

from .agents import clarifier_agent, upper_agent


def build_app(agents: Sequence[Agent], *, name: str = "a2a-example") -> FastAPI:
    """A FastAPI app serving `agents` over A2A, mounted at the root."""
    app = FastAPI(title=name)
    app.include_router(
        create_a2a_router(
            list(agents),
            name=name,
            description="A reactifact agent, exposed over A2A.",
        )
    )
    return app


def create_upper_app() -> FastAPI:
    return build_app([upper_agent()], name="upper-agent")


def create_clarifier_app() -> FastAPI:
    return build_app([clarifier_agent()], name="clarifier-agent")


__all__ = ["build_app", "create_clarifier_app", "create_upper_app"]
