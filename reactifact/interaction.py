"""Non-data collaborators injected into steps.

`Effects` stays the low-level write surface. `Human` and `Outbox` are the
high-level collaborators for the two things that are not data writes:

* `Human.ask(...)` poses a question to a person and creates a
  `PendingQuestion`; `Human.resume(...)` records the answer.
* `Outbox.act(...)` records an outbound side-effect intent for the dispatcher.

Both are thin views over the current step's `Effects`, so they still compile
into the same atomic patch.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .effects import Effects, Handle
from .interrupt import PendingAction, PendingQuestion, schema_json


class Human:
    """HITL collaborator: ask a person, record the answer."""

    __slots__ = ("_effects",)

    def __init__(self, effects: Effects):
        self._effects = effects

    def ask(
        self,
        question: str,
        *,
        kind: str = "general",
        notes: dict[str, Any] | None = None,
        schema: Mapping[str, Any] | type | None = None,
        id: str | None = None,
    ) -> Handle:
        """Creates a `PendingQuestion`; the answer arrives via `resume(...)`."""
        return self._effects.create(
            PendingQuestion(
                question=question,
                kind=kind,
                notes=dict(notes or {}),
                resolution_schema=schema_json(schema),
            ),
            id=id,
        )

    def resume(self, question: Any, answer: Any) -> Effects:
        """Records the human answer on a `PendingQuestion`."""
        return self._effects._resume(question, answer)


class Outbox:
    """Outbound side-effect collaborator: record intent, never perform I/O."""

    __slots__ = ("_effects",)

    def __init__(self, effects: Effects):
        self._effects = effects

    def act(
        self,
        kind: str,
        *,
        key: str,
        payload: dict[str, Any] | None = None,
        id: str | None = None,
    ) -> Handle | None:
        """Records a `PendingAction` for `Runtime(dispatcher=...)`."""
        data = PendingAction(
            kind=kind, idempotency_key=key, payload=dict(payload or {})
        )
        return self._effects.create_once(data, id=id or f"action:{key}")


__all__ = ["Human", "Outbox"]
