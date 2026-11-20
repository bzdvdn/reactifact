from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel, Field


def schema_json(schema: Mapping[str, Any] | type | None) -> dict[str, Any] | None:
    """Normalizes a HITL answer schema to JSON Schema (or `None`).

    Accepts a Pydantic model class (its `model_json_schema()`) or an already
    built mapping. The schema is stored on the question for a UI to render;
    it is not enforced by core at resume time (a typed `BaseModel` answer is
    validated by its own class before it reaches `effects.resume`).
    """
    if schema is None:
        return None
    if isinstance(schema, Mapping):
        return dict(schema)
    if isinstance(schema, type) and hasattr(schema, "model_json_schema"):
        return cast("dict[str, Any]", schema.model_json_schema())
    raise TypeError("schema must be a Pydantic model class or a JSON-schema mapping")


def answer_payload(answer: Any) -> tuple[str, dict[str, Any] | None]:
    """Splits a HITL answer into `(legacy text, typed data)`.

    A plain string keeps the old behavior (`resolution` only). A Pydantic model
    or a mapping is dumped to JSON-safe data and also mirrored into
    `resolution` as JSON, so older readers still find a string.
    """
    if isinstance(answer, str):
        return answer, None
    if isinstance(answer, BaseModel):
        data = answer.model_dump(mode="json")
    elif isinstance(answer, Mapping):
        data = dict(answer)
    else:
        raise TypeError("answer must be str, a Pydantic model, or a mapping")
    return json.dumps(data, ensure_ascii=False, sort_keys=True), data


class PendingQuestion(BaseModel):
    """Artifact awaiting a human response (HITL, constitution §60).

    Created by an agent (or directly) to block a step until user input.
    A human answer is recorded via `effects.resume(question, answer)` (§60),
    after which agents subscribed to `PendingQuestion(answered=True)` continue.

    `resolution` stays the legacy textual answer. When the question declares a
    `resolution_schema`, the app may resume with a Pydantic model or a mapping:
    the JSON-safe value lands in `resolution_data`, and `resolution` mirrors it
    as JSON for older readers.
    """

    question: str
    kind: str = "general"
    notes: dict[str, Any] = Field(default_factory=dict)
    answered: bool = False
    resolution: str | None = None
    #: Typed human answer (JSON-safe) when the question declared a schema.
    resolution_data: dict[str, Any] | None = None
    #: JSON Schema the UI can render/validate the answer against.
    resolution_schema: dict[str, Any] | None = None
    resolved_at: datetime | None = None

    #: Wall-clock fields excluded from `context_hash` (transactional envelope,
    #: not state): a resolved question's *state* is `resolution`/
    #: `resolution_data`, the moment it was resolved is not something two
    #: equivalent runs must agree on.
    transient_fields: ClassVar[frozenset[str]] = frozenset({"resolved_at"})

    def answer_text(self) -> str:
        """A human-readable answer: `resolution`, or a text field from typed data."""
        if self.resolution_data is not None:
            approved = self.resolution_data.get("approved")
            if isinstance(approved, bool):
                return "yes" if approved else "no"
            for key in ("text", "answer", "value", "note"):
                value = self.resolution_data.get(key)
                if isinstance(value, str):
                    return value
            return json.dumps(self.resolution_data, ensure_ascii=False, sort_keys=True)
        return self.resolution or ""

    def approved(self) -> bool:
        """Whether an approval answer is affirmative.

        Prefers a typed `{"approved": true}` (the schema most approval UIs use)
        and falls back to the legacy yes/no strings.
        """
        if isinstance(self.resolution_data, dict):
            value = self.resolution_data.get("approved")
            if isinstance(value, bool):
                return value
        return (self.resolution or "").strip().lower() in (
            "yes",
            "y",
            "approve",
            "approved",
            "true",
        )


class PendingAction(BaseModel):
    """An outbound side effect awaiting dispatch (outbox, §42/§55/§59).

    A produce must not perform external I/O itself — send a notification, call
    a webhook, deploy. It records the *intent* here via
    `effects.act(kind=..., payload=..., key=...)`; the runtime dispatches
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

    #: Wall-clock field excluded from `context_hash` (transactional envelope,
    #: not state): the *fact* that the action dispatched (`status`/`error`/
    #: `attempts`) is hashed, the moment it happened is not — so an outbox run
    #: stays run-to-run reproducible.
    transient_fields: ClassVar[frozenset[str]] = frozenset({"dispatched_at"})
