"""Shared web-adapter pieces: SSE, error mapping and route config."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel

from ..chat import ChatEvent, ChatEventKind

#: Canonical wire event names, one per `ChatEvent.kind`.
DEFAULT_EVENT_NAMES: dict[ChatEventKind, str] = {
    "session": "session",
    "status": "status",
    "message": "message",
}


def _default_payload(event: ChatEvent) -> Mapping[str, Any]:
    """The canonical frame payloads (unchanged from before configurability)."""
    if event.kind == "session":
        return {"session_id": event.session_id}
    if event.kind == "status":
        return {"message": event.message}
    return event.payload or {"reply": ""}


class ChatMessage(BaseModel):
    """Wire shape of an incoming user turn."""

    message: str
    session_id: str = ""


def sse(event_type: str, data: dict[str, Any]) -> str:
    """Formats one Server-Sent-Events frame."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def install_error_handlers(
    app: Any,
    *,
    extra: Mapping[type[Exception], int] | None = None,
) -> None:
    """Map reactifact domain errors to JSON HTTP responses.

    Call once on the FastAPI app (before serving). Defaults:
    `AuthorizationError` → 403, `GuardrailViolation` → 422,
    `QuotaExceeded` → 429, `MergeConflict` → 409. `extra` adds or overrides
    mappings (`{MyError: 418}`).

    FastAPI is imported lazily, like the rest of this module.
    """
    from fastapi.responses import JSONResponse

    from ..authz import AuthorizationError
    from ..branching import MergeConflict
    from ..guardrails import GuardrailViolation
    from ..quota import QuotaExceeded

    statuses: dict[type[Exception], int] = {
        AuthorizationError: 403,
        GuardrailViolation: 422,
        QuotaExceeded: 429,
        MergeConflict: 409,
    }
    if extra:
        statuses.update(extra)

    for exc_type, status in statuses.items():

        async def handler(
            request: Any, exc: Exception, _status: int = status
        ) -> JSONResponse:
            return JSONResponse(
                status_code=_status,
                content={"error": type(exc).__name__, "detail": str(exc)},
            )

        app.add_exception_handler(exc_type, handler)


@dataclass(frozen=True)
class PipelineRoutes:
    """Paths of the typed Pipeline HTTP adapter (relative to its prefix).

    Set a field to `None` to leave that route out and add your own.
    """

    run: str | None = "/run"
    stream: str | None = "/stream"
    health: str | None = "/health"


@dataclass(frozen=True)
class ChatRoutes:
    """Paths of the chat HTTP adapter (relative to its prefix).

    Defaults keep the historical canonical contract (`/chat/stream`,
    `/runs/{session_id}`). Pass a mapping/dataclass to match an existing API:

        ChatRoutes(
            stream="/chat/stream",
            invoke="/chat",
            history="/chat/history",
            clear="/chat/clear",
            clear_method="POST",
            history_messages=True,
        )

    `history`/`clear` may contain `{session_id}` (path parameter) or not (query
    parameter). `invoke` adds a non-streaming `POST`; `history_messages=True`
    returns the thread as a list instead of the full history mapping.
    """

    stream: str | None = "/chat/stream"
    invoke: str | None = None
    history: str | None = "/runs/{session_id}"
    clear: str | None = "/runs/{session_id}"
    health: str | None = "/health"
    clear_method: str = "DELETE"
    history_messages: bool = False


def _routes(default: Any, override: Any | None, kind: type[Any]) -> Any:
    if override is None:
        return default
    if isinstance(override, kind):
        return override
    return replace(default, **override)


def _check_path(value: str | None, name: str) -> None:
    if value is not None and not value.startswith("/"):
        raise ValueError(f"{name} route must start with '/': {value!r}")
