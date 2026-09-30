from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PendingQuestion(BaseModel):
    """Artifact awaiting a human response (HITL, constitution §60).

    Created by an agent (or directly) to block a step until user input.
    A human answer is recorded via `self.effects.resume(question, answer)` (§60),
    after which agents subscribed to `PendingQuestion(answered=True)` continue.
    """

    question: str
    kind: str = "general"
    notes: dict[str, Any] = Field(default_factory=dict)
    answered: bool = False
    resolution: str | None = None
    resolved_at: datetime | None = None


class PendingAction(BaseModel):
    """An outbound side effect awaiting dispatch (outbox, §42/§55/§59).

    A produce must not perform external I/O itself — send a notification, call
    a webhook, deploy. It records the *intent* here via
    `self.effects.act(kind=..., payload=..., key=...)`; the runtime dispatches
    it (through `Runtime(dispatcher=...)`) once the intent is **committed**.
    That split is what makes the environment effect safe under replay/retry:

    - replay reconstructs the record without running produces, so it never
      re-sends;
    - a retried produce re-derives the same stable id (`action:{key}`), so
      `effects.act` returns `None` and no second intent is created;
    - two branches that independently reach the same action share one id and
      merge to a single intent (equal data is a no-op, §40).

    `idempotency_key` is passed to the external system so the at-least-once
    window (crash after the I/O, before the `dispatched` commit) is closed on
    the far side; `kind` routes the dispatcher to the right handler.
    """

    kind: str
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "dispatched", "failed"] = "pending"
    #: How many dispatch attempts have failed so far (visibility only — retry
    #: policy/backoff belongs to the application or the dispatcher, not core).
    attempts: int = 0
    dispatched_at: datetime | None = None
    error: str | None = None
