from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal


@dataclass
class ProgressEvent:
    """Progress event that agents publish into the stream (aggregate statuses).

    Candidate kinds: "status" (Thinking…, Searching in …, Found N…, Composing answer…).
    The app renders them in the chat; `data` holds the details (source, count, …).
    """

    kind: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }


# ---- typed run events (§54) ------------------------------------------------- #

RunEventMode = Literal["custom", "updates", "values", "debug", "messages"]

#: Every mode `Runtime.astream_events` understands.
RUN_EVENT_MODES: frozenset[str] = frozenset(
    {"custom", "updates", "values", "debug", "messages"}
)


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, **fields}


@dataclass
class RunStart:
    run_id: str
    session_id: str = ""
    scope: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    kind: ClassVar[str] = "run_start"

    def to_dict(self) -> dict[str, Any]:
        return _event(
            self.kind,
            run_id=self.run_id,
            session_id=self.session_id,
            scope=self.scope,
            metadata=self.metadata,
            started_at=self.started_at.isoformat(),
        )


@dataclass
class GenerationStart:
    run_id: str
    generation: int
    kind: ClassVar[str] = "generation_start"

    def to_dict(self) -> dict[str, Any]:
        return _event(self.kind, run_id=self.run_id, generation=self.generation)


@dataclass
class AgentStart:
    run_id: str
    agent: str
    event_type: str = ""
    event_artifact_id: str = ""
    reads: list[dict[str, Any]] = field(default_factory=list)
    kind: ClassVar[str] = "agent_start"

    def to_dict(self) -> dict[str, Any]:
        return _event(
            self.kind,
            run_id=self.run_id,
            agent=self.agent,
            event_type=self.event_type,
            event_artifact_id=self.event_artifact_id,
            reads=self.reads,
        )


@dataclass
class AgentRetry:
    run_id: str
    agent: str
    attempt: int
    error: str
    kind: ClassVar[str] = "agent_retry"

    def to_dict(self) -> dict[str, Any]:
        return _event(
            self.kind,
            run_id=self.run_id,
            agent=self.agent,
            attempt=self.attempt,
            error=self.error,
        )


@dataclass
class AgentEnd:
    run_id: str
    agent: str
    latency_ms: float = 0.0
    error: str | None = None
    tools: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    kind: ClassVar[str] = "agent_end"

    def to_dict(self) -> dict[str, Any]:
        return _event(
            self.kind,
            run_id=self.run_id,
            agent=self.agent,
            latency_ms=self.latency_ms,
            error=self.error,
            tools=self.tools,
            writes=self.writes,
        )


@dataclass
class ToolStart:
    run_id: str
    tool: str
    args: dict[str, Any] | None = None
    kind: ClassVar[str] = "tool_start"

    def to_dict(self) -> dict[str, Any]:
        return _event(self.kind, run_id=self.run_id, tool=self.tool, args=self.args)


@dataclass
class ToolEnd:
    run_id: str
    tool: str
    ok: bool = True
    latency_ms: float = 0.0
    error: str | None = None
    chars: int = 0
    kind: ClassVar[str] = "tool_end"

    def to_dict(self) -> dict[str, Any]:
        return _event(
            self.kind,
            run_id=self.run_id,
            tool=self.tool,
            ok=self.ok,
            latency_ms=self.latency_ms,
            error=self.error,
            chars=self.chars,
        )


@dataclass
class ArtifactWrite:
    run_id: str
    commit_id: str
    version: int
    author: str
    message: str
    writes: list[dict[str, Any]] = field(default_factory=list)
    kind: ClassVar[str] = "artifact_write"

    def to_dict(self) -> dict[str, Any]:
        return _event(
            self.kind,
            run_id=self.run_id,
            commit_id=self.commit_id,
            version=self.version,
            author=self.author,
            message=self.message,
            writes=self.writes,
        )


@dataclass
class Values:
    run_id: str
    version: int
    diff: dict[str, Any] = field(default_factory=dict)
    kind: ClassVar[str] = "values"

    def to_dict(self) -> dict[str, Any]:
        return _event(
            self.kind, run_id=self.run_id, version=self.version, diff=self.diff
        )


@dataclass
class LLMDelta:
    run_id: str
    agent: str
    step: str
    delta: str
    provider: str = ""
    model: str = ""
    streamed: bool = True
    kind: ClassVar[str] = "llm_delta"

    def to_dict(self) -> dict[str, Any]:
        return _event(
            self.kind,
            run_id=self.run_id,
            agent=self.agent,
            step=self.step,
            delta=self.delta,
            provider=self.provider,
            model=self.model,
            streamed=self.streamed,
        )


@dataclass
class CustomEvent:
    run_id: str
    event: ProgressEvent
    kind: ClassVar[str] = "custom"

    def to_dict(self) -> dict[str, Any]:
        return _event(self.kind, run_id=self.run_id, event=self.event.to_dict())


@dataclass
class RunEnd:
    run_id: str
    outcome: str
    runs: int = 0
    duration_ms: float = 0.0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    kind: ClassVar[str] = "run_end"

    def to_dict(self) -> dict[str, Any]:
        return _event(
            self.kind,
            run_id=self.run_id,
            outcome=self.outcome,
            runs=self.runs,
            duration_ms=self.duration_ms,
            errors=self.errors,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cost=self.cost,
        )


@dataclass
class RunFailed:
    run_id: str
    error: str
    kind: ClassVar[str] = "run_failed"

    def to_dict(self) -> dict[str, Any]:
        return _event(self.kind, run_id=self.run_id, error=self.error)


#: Every typed event `Runtime.astream_events` can yield.
RunEvent = (
    RunStart
    | GenerationStart
    | AgentStart
    | AgentRetry
    | AgentEnd
    | ToolStart
    | ToolEnd
    | ArtifactWrite
    | Values
    | LLMDelta
    | CustomEvent
    | RunEnd
    | RunFailed
)


QueueEvent = asyncio.Queue[ProgressEvent]

#: Per-subscriber queue bound. Status events are best-effort UI hints: a
#: subscriber that never drains must not grow the queue without bound, so the
#: oldest event is dropped when this is reached.
_DEFAULT_SUBSCRIBER_QUEUE_SIZE = 1024


class EventHub:
    """Broadcaster of agent status events into a single- or multi-stream.

    Publishing without subscribers is a no-op, so ordinary (non-streaming) runtime
    runs pay nothing for announce.
    """

    def __init__(self, maxsize: int = _DEFAULT_SUBSCRIBER_QUEUE_SIZE) -> None:
        self._subscribers: set[QueueEvent] = set()
        self._maxsize = maxsize

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def subscribe(self) -> QueueEvent:
        queue: QueueEvent = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: QueueEvent) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: ProgressEvent) -> None:
        for queue in self._subscribers:
            if queue.full():
                # Drop the oldest status hint rather than block or grow forever.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)


__all__ = [
    "AgentEnd",
    "AgentRetry",
    "AgentStart",
    "ArtifactWrite",
    "CustomEvent",
    "EventHub",
    "GenerationStart",
    "LLMDelta",
    "ProgressEvent",
    "QueueEvent",
    "RUN_EVENT_MODES",
    "RunEnd",
    "RunEvent",
    "RunEventMode",
    "RunFailed",
    "RunStart",
    "ToolEnd",
    "ToolStart",
    "Values",
]
