"""Chat's specialization of the generic `Invocation`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ...invocation import Invocation


@dataclass(frozen=True)
class Turn:
    """One chat turn: an invocation plus its session id.

    Pass `turn.invocation` to `Chat.stream`/`Chat.invoke` for retry-safe
    idempotency; core execution only needs the `Invocation`.
    """

    invocation: Invocation
    session_id: str

    @classmethod
    def new(
        cls,
        session_id: str = "",
        *,
        idempotency_key: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> Turn:
        return cls(
            invocation=Invocation.new(
                idempotency_key=idempotency_key,
                scope=session_id,
                metadata=metadata,
            ),
            session_id=session_id,
        )

    @property
    def id(self) -> str:
        return self.invocation.id


__all__ = ["Turn"]
