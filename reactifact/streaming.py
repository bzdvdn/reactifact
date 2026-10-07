from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


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
