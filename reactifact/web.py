"""reactifact.web — web adapter over the chat layer (FastAPI, SSE).

Because every app already owns its FastAPI instance, this module ships an
`APIRouter`, not an app. Mount it wherever:

    from reactifact import ChatAssistant, SessionStore
    from reactifact.web import create_chat_router

    app.include_router(create_chat_router(assistant))

The wire contract is the canonical chat (owned by `reactifact.chat`):
``session`` → ``status``… → ``message`` over Server-Sent Events, plus the
standard runs (list) / deletion endpoints.

If your `ChatAssistant` was built with a *shared* `resources=` instance (not
a callable — see its docstring), that instance's provider owns an HTTP
client for the app's lifetime; close it in your own FastAPI shutdown, since
this module owns only the router, not the app:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await resources.aclose()

    app = FastAPI(lifespan=lifespan)

FastAPI is imported lazily (via `reactifact._extras`): the module imports without
fastapi installed, and only `create_chat_router` requires the `web` extra —
`pip install "reactifact[web]"`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ._extras import require_extra
from .chat import ChatAssistant, ChatEvent, ChatEventKind

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse, StreamingResponse


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


def create_chat_router(
    assistant: ChatAssistant,
    *,
    prefix: str = "/api",
    with_health: bool = True,
    event_names: Mapping[str, str] | None = None,
    forward_kinds: Sequence[str] | None = None,
    payload_shaper: Callable[[ChatEvent], Mapping[str, Any]] | None = None,
    done_event: str | None = None,
) -> APIRouter:
    """Builds the chat router on top of a `ChatAssistant`.

    Routes (default prefix `/api`):
      POST   /api/chat/stream       — SSE turn (session → status… → message)
      GET    /api/runs/{id}         — reconstructed chat thread
      DELETE /api/runs/{id}         — delete a session's history
      GET    /api/health            — liveness (opt-out via `with_health=False`)

    The SSE *vocabulary* is configurable, so a client with its own wire format
    (say `status`/`content`/`done`) is a configuration, not a fork:

    - `event_names` — rename frame kinds: `{"message": "content"}`.
    - `forward_kinds` — emit only these `ChatEvent.kind`s (drop `session`, …).
    - `payload_shaper(event) -> mapping` — reshape a frame's payload.
    - `done_event` — emit one extra terminal frame (e.g. `"done"`) when the
      stream ends.

    Defaults reproduce the canonical contract exactly, so existing clients are
    unaffected. The event schema (`ChatEvent`) and the effective names are
    published in the route's OpenAPI `responses`.
    """
    # Readable error when the `web` extra is missing — then a regular
    # (mypy-visible) import for the real types.
    require_extra("web.create_chat_router", "fastapi", "web")
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse, StreamingResponse

    names: dict[str, str] = {k: v for k, v in DEFAULT_EVENT_NAMES.items()}
    names.update(event_names or {})
    forward = set(forward_kinds) if forward_kinds is not None else None
    shaper = payload_shaper or _default_payload

    router = APIRouter(prefix=prefix)

    if with_health:

        @router.get("/health")
        async def health() -> dict[str, bool]:
            return {"ok": True}

    @router.post(
        "/chat/stream",
        response_model=None,
        responses={
            200: {
                "description": (
                    "Server-Sent Events. Frame kinds → event names: "
                    + ", ".join(f"{kind}={name}" for kind, name in names.items())
                    + ("; terminal: " + done_event if done_event else "")
                ),
                "content": {
                    "text/event-stream": {"schema": ChatEvent.model_json_schema()}
                },
            }
        },
    )
    async def chat_stream(req: ChatMessage) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            async for event in assistant.stream(req.message, req.session_id):
                if forward is not None and event.kind not in forward:
                    continue
                yield sse(names.get(event.kind, event.kind), dict(shaper(event)))
            if done_event:
                yield sse(done_event, {})

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/runs/{session_id}", response_model=None)
    async def runs(session_id: str) -> JSONResponse:
        return JSONResponse(await assistant.history(session_id))

    @router.delete("/runs/{session_id}")
    async def run_delete(session_id: str) -> dict[str, bool]:
        await assistant.store.delete_session(session_id)
        return {"ok": True}

    return router


__all__ = ["DEFAULT_EVENT_NAMES", "ChatMessage", "create_chat_router", "sse"]
