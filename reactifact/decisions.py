"""Small decision records for explainable reactive execution.

`Agent`/`Runtime` emit these while a run executes; `RunTracer` collects them
into `RunTrace.decisions`. They answer "why didn't this step run?" without
turning every non-match into a full span.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

DecisionSink = Callable[["Decision"], None]


class Decision(BaseModel):
    """One scheduling decision: ran/skipped/stage/budget.

    `code` is a stable machine label (`skipped_guard`, `skipped_missing_input`,
    `event_unmatched`, `stage_enter`, `stage_settled`, `fallback`, …).
    """

    code: str
    agent: str = ""
    step: str = ""
    reason: str = ""
    #: Extra machine-readable detail (guard code, parameter name, stage).
    detail: dict[str, str] = Field(default_factory=dict)
    event_type: str = ""
    event_artifact_type: str = ""
    event_artifact_id: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


_SINK: ContextVar[DecisionSink | None] = ContextVar(
    "reactifact_decision_sink", default=None
)


def build_decision(
    code: str,
    *,
    agent: str = "",
    step: str = "",
    reason: str = "",
    event: Any | None = None,
    detail: dict[str, str] | None = None,
) -> Decision:
    """Build a `Decision` from an event and free-form fields."""
    event_type = ""
    event_artifact_type = ""
    event_artifact_id = ""
    if event is not None:
        event_type = getattr(getattr(event, "type", None), "value", "") or str(
            getattr(event, "type", "")
        )
        artifact_type = getattr(event, "artifact_type", None)
        event_artifact_type = (
            artifact_type.__name__
            if isinstance(artifact_type, type)
            else str(artifact_type or "")
        )
        event_artifact_id = str(getattr(event, "artifact_id", "") or "")
    return Decision(
        code=code,
        agent=agent,
        step=step,
        reason=reason,
        detail=dict(detail or {}),
        event_type=event_type,
        event_artifact_type=event_artifact_type,
        event_artifact_id=event_artifact_id,
    )


def record_decision(
    code: str,
    *,
    agent: str = "",
    step: str = "",
    reason: str = "",
    event: Any | None = None,
    detail: dict[str, str] | None = None,
) -> None:
    """Emit a decision to the current runtime's trace sink, if any."""
    sink = _SINK.get()
    if sink is None:
        return
    try:
        sink(
            build_decision(
                code,
                agent=agent,
                step=step,
                reason=reason,
                event=event,
                detail=detail,
            )
        )
    except Exception:  # noqa: BLE001 — observability must never break a run
        return


def set_decision_sink(sink: DecisionSink | None) -> Token[DecisionSink | None]:
    return _SINK.set(sink)


def reset_decision_sink(token: Token[DecisionSink | None]) -> None:
    _SINK.reset(token)


__all__ = [
    "Decision",
    "DecisionSink",
    "build_decision",
    "record_decision",
    "reset_decision_sink",
    "set_decision_sink",
]
