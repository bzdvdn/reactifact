"""Agent containers for the fintech audit pipeline."""

from __future__ import annotations

from reactifact import Agent, Consume, create_agent
from reactifact.sources import SourceRef

from examples.fintech_audit.models import (
    Policy,
    Question,
    Spend,
    Table,
    Variance,
)
from examples.fintech_audit.produce import (
    answer,
    compute_spend,
    compute_variance,
    materialize,
    search_sources,
)

AGENTS: list[Agent] = [
    create_agent("search", consumes=[Consume(Question)], produces=[search_sources]),
    create_agent("materialize", consumes=[Consume(SourceRef)], produces=[materialize]),
    create_agent("analyze", consumes=[Consume(Table)], produces=[compute_spend]),
    create_agent("variance", consumes=[Consume(Spend)], produces=[compute_variance]),
    create_agent(
        "answer",
        consumes=[Consume(Variance), Consume(Policy, wakes=False)],
        produces=[answer],
    ),
]

__all__ = ["AGENTS"]
