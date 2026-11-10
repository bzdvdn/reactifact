"""Dialect-neutral trace row mapping and serialization helpers."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from ..tools import ToolRun
from .models import (
    AgentContract,
    AgentSpan,
    ArtifactRef,
    LLMCall,
    RelationRef,
    RunTrace,
    StepRun,
    TagAssignment,
)

#: Sort keys the dashboard may request; anything else falls back to started_at.
SORT_COLUMNS = {
    "started_at": "started_at",
    "duration_ms": "duration_ms",
    "outcome": "outcome",
    "session_id": "session_id",
    "spans": "spans_count",
}


def like(query: str) -> str:
    """Escape a user search into a LIKE pattern (paired with ESCAPE '!')."""
    escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def iso(value: float | datetime | None) -> str:
    """Normalize a stored timestamp to ISO-8601 for dashboard JSON."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    else:
        moment = datetime.fromtimestamp(value, tz=UTC)
    return moment.isoformat()


def as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.fromtimestamp(float(value), tz=UTC)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def run_insert_values(
    trace: RunTrace,
    *,
    timestamp: Callable[[datetime], Any],
) -> tuple[Any, ...]:
    started = trace.started_at or datetime.now(UTC)
    prompt_tokens = sum(call.prompt_tokens for call in trace.llm_calls)
    completion_tokens = sum(call.completion_tokens for call in trace.llm_calls)
    return (
        trace.id,
        trace.session_id,
        timestamp(started),
        trace.duration_ms,
        trace.outcome,
        prompt_tokens,
        completion_tokens,
        1 if trace.trace_truncated else 0,
    )


def span_insert_values(
    trace_id: str,
    span: AgentSpan,
    *,
    timestamp: Callable[[datetime], Any],
    jsonify: Callable[[Any], Any],
) -> tuple[Any, ...]:
    started = span.started_at
    return (
        trace_id,
        span.agent,
        span.event_type,
        span.event_artifact_type,
        span.latency_ms,
        span.error,
        timestamp(started) if started is not None else None,
        jsonify([read.model_dump(mode="json") for read in span.reads]),
        jsonify([write.model_dump(mode="json") for write in span.writes]),
        jsonify([rel.model_dump(mode="json") for rel in span.relations]),
        jsonify([call.model_dump(mode="json") for call in span.llm_calls]),
        jsonify([run.model_dump(mode="json") for run in span.produces]),
        jsonify([tool.model_dump(mode="json") for tool in span.tools]),
        span.event_artifact_id,
        jsonify(span.contract.model_dump(mode="json")),
    )


def span_from_row(row: Sequence[Any]) -> AgentSpan:
    return AgentSpan(
        agent=row[0],
        event_type=row[1],
        event_artifact_type=row[2],
        latency_ms=row[3],
        error=row[4],
        started_at=as_datetime(row[5]),
        reads=[ArtifactRef(**item) for item in _json(row[6])],
        writes=[ArtifactRef(**item) for item in _json(row[7])],
        relations=[RelationRef(**item) for item in _json(row[8])],
        llm_calls=[LLMCall(**item) for item in _json(row[9])],
        produces=[StepRun(**item) for item in _json(row[10])],
        tools=[ToolRun(**item) for item in _json(row[11])],
        event_artifact_id=row[12],
        contract=AgentContract(**_json(row[13])),
    )


def tag_assignment_from_row(row: Sequence[Any]) -> TagAssignment:
    created_at = as_datetime(row[3])
    updated_at = as_datetime(row[4])
    assert created_at is not None and updated_at is not None
    return TagAssignment(
        tag=row[0],
        note=row[1],
        color=row[2],
        created_at=created_at,
        updated_at=updated_at,
    )


__all__ = [
    "SORT_COLUMNS",
    "as_datetime",
    "iso",
    "like",
    "run_insert_values",
    "span_from_row",
    "span_insert_values",
    "tag_assignment_from_row",
]
