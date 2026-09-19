"""Agent containers for support_copilot."""

from __future__ import annotations

from reactifact import Agent, Consume, PendingQuestion, create_agent
from reactifact.sources import SourceRef

from examples.support_copilot.models import Doc, Question, SearchDone
from examples.support_copilot.produce import (
    answer,
    escalate,
    finalize,
    resolve,
    search,
)

AGENTS: list[Agent] = [
    create_agent("search", consumes=[Consume(Question)], produces=[search]),
    create_agent("resolve", consumes=[Consume(SourceRef)], produces=[resolve]),
    create_agent("answer", consumes=[Consume(Doc)], produces=[answer]),
    create_agent("escalate", consumes=[Consume(SearchDone)], produces=[escalate]),
    create_agent("finalize", consumes=[Consume(PendingQuestion)], produces=[finalize]),
]

__all__ = ["AGENTS"]
