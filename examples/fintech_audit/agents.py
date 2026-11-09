"""Agent containers for the fintech audit pipeline."""

from __future__ import annotations

from reactifact import Agent, create_agent

from examples.fintech_audit.steps import (
    answer,
    compute_spend,
    compute_variance,
    materialize,
    search_sources,
)

AGENTS: list[Agent] = [
    create_agent("search", steps=[search_sources]),
    create_agent("materialize", steps=[materialize]),
    create_agent("analyze", steps=[compute_spend]),
    create_agent("variance", steps=[compute_variance]),
    create_agent("answer", steps=[answer]),
]

__all__ = ["AGENTS"]
