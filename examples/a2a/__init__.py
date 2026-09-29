"""A2A example — a reactifact agent served over Agent2Agent, and called back.

Two deterministic (no-LLM) agents, a small FastAPI app per agent, and a runnable
demo that exercises both directions in-process (no network):

    .venv/bin/python -m examples.a2a.demo

See `README.md` for the walkthrough.
"""

from __future__ import annotations

from .agents import clarifier_agent, upper_agent
from .server import build_app, create_clarifier_app, create_upper_app

__all__ = [
    "build_app",
    "clarifier_agent",
    "create_clarifier_app",
    "create_upper_app",
    "upper_agent",
]
