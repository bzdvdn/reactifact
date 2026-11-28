"""Generic invocation identity: one run inside a session.

`Invocation` is transport-neutral: a webhook id, a daemon tick, a batch item,
an A2A message or a chat turn. `Turn` in `recipes.conversation` is the chat
specialization; core code only needs the id/idempotency key/scope/metadata.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Invocation:
    """Identity of one execution.

    `idempotency_key` is optional and stable across retries; a store can use it
    to replay the previous result instead of running the same invocation twice.
    `scope` is a workspace/tenant/session label; `metadata` is for tracing and
    adapters (webhook name, category, …), never secrets.
    """

    id: str
    idempotency_key: str | None = None
    scope: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Invocation.id must be a non-empty string")
        if self.idempotency_key is not None and not self.idempotency_key:
            raise ValueError("Invocation.idempotency_key must be non-empty or None")

    @classmethod
    def new(
        cls,
        *,
        id: str | None = None,
        idempotency_key: str | None = None,
        scope: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> Invocation:
        return cls(
            id=id or uuid.uuid4().hex,
            idempotency_key=idempotency_key,
            scope=scope,
            metadata=dict(metadata or {}),
        )


_CURRENT: ContextVar[Invocation | None] = ContextVar(
    "reactifact_invocation", default=None
)


def current_invocation() -> Invocation | None:
    """The invocation of the running turn, or None outside a run."""
    return _CURRENT.get()


def set_invocation(invocation: Invocation | None) -> Token[Invocation | None]:
    return _CURRENT.set(invocation)


def reset_invocation(token: Token[Invocation | None]) -> None:
    _CURRENT.reset(token)


__all__ = [
    "Invocation",
    "current_invocation",
    "reset_invocation",
    "set_invocation",
]
