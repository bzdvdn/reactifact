"""FastAPI chat adapter: sessions, history and SSE."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .._extras import require_extra
from ..chat import Chat, ChatAssistant, ChatEvent
from .common import (
    DEFAULT_EVENT_NAMES,
    ChatMessage,
    ChatRoutes,
    _check_path,
    _default_payload,
    _routes,
    install_error_handlers,
    sse,
)

if TYPE_CHECKING:
    from fastapi import APIRouter, Request


def create_chat_router(
    assistant: Chat | ChatAssistant,
    *,
    prefix: str = "/api",
    routes_override: ChatRoutes | Mapping[str, Any] | None = None,
    with_health: bool = True,
    event_names: Mapping[str, str] | None = None,
    forward_kinds: Sequence[str] | None = None,
    payload_shaper: Callable[[ChatEvent], Mapping[str, Any]] | None = None,
    done_event: str | None = None,
    request_factory: Callable[
        [Request], Mapping[str, Any] | Awaitable[Mapping[str, Any]]
    ]
    | None = None,
) -> APIRouter:
    """Builds the chat router on top of a `Chat` or `ChatAssistant`.

    Routes (default prefix `/api`):
      POST   /api/chat/stream       — SSE turn (session → status… → message)
      GET    /api/runs/{id}         — reconstructed chat thread
      DELETE /api/runs/{id}         — delete a session's history
      GET    /api/health            — liveness (opt-out via `with_health=False`)

    `routes=` overrides the paths (and can add `POST /chat` + `/chat/history` /
    `/chat/clear`) without changing the wire contract. See `ChatRoutes`.

    The SSE *vocabulary* is configurable, so a client with its own wire format
    (say `status`/`content`/`done`) is a configuration, not a fork:

    - `event_names` — rename frame kinds: `{"message": "content"}`.
    - `forward_kinds` — emit only these `ChatEvent.kind`s (drop `session`, …).
    - `payload_shaper(event) -> mapping` — reshape a frame's payload.
    - `done_event` — emit one extra terminal frame (e.g. `"done"`) when the
      stream ends.
    - `request_factory(request) -> mapping` — build the per-turn `request=`
      mapping (the same one `ChatAssistant.stream` takes) from the incoming
      FastAPI `Request`, e.g. the authenticated user for ACL/identity-aware
      runs. May be sync or async (an awaitable result is awaited). Without it
      the turn runs with `request=None`.

    Defaults reproduce the canonical contract exactly, so existing clients are
    unaffected. The event schema (`ChatEvent`) and the effective names are
    published in the route's OpenAPI `responses`.
    """
    # Readable error when the `web` extra is missing — then a regular
    # (mypy-visible) import for the real types.
    require_extra("web.create_chat_router", "fastapi", "web")
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    # Endpoint annotations are strings (`from __future__ import annotations`);
    # publish the response classes so FastAPI/OpenAPI can resolve them.
    globals().setdefault("Request", Request)
    globals().setdefault("JSONResponse", JSONResponse)
    globals().setdefault("StreamingResponse", StreamingResponse)

    routes = cast(ChatRoutes, _routes(ChatRoutes(), routes_override, ChatRoutes))
    if not with_health:
        routes = replace(routes, health=None)
    for name in ("stream", "invoke", "history", "clear", "health"):
        _check_path(getattr(routes, name), f"chat {name}")
    if routes.clear_method not in ("DELETE", "POST"):
        raise ValueError(
            f"ChatRoutes.clear_method must be DELETE or POST, got "
            f"{routes.clear_method!r}"
        )

    names: dict[str, str] = {k: v for k, v in DEFAULT_EVENT_NAMES.items()}
    names.update(event_names or {})
    forward = set(forward_kinds) if forward_kinds is not None else None
    shaper = payload_shaper or _default_payload

    async def _turn_request(request: Any) -> Mapping[str, Any] | None:
        if request_factory is None:
            return None
        value = request_factory(request)
        if inspect.isawaitable(value):
            value = await value
        return value

    router = APIRouter(prefix=prefix)

    if routes.health is not None:

        @router.get(routes.health)
        async def health() -> dict[str, bool]:
            return {"ok": True}

    if routes.stream is not None:

        async def chat_stream(req: ChatMessage, request: Request) -> StreamingResponse:
            turn_request = await _turn_request(request)

            async def stream() -> AsyncIterator[str]:
                async for event in assistant.stream(
                    req.message, req.session_id, request=turn_request
                ):
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

        chat_stream.__annotations__ = {
            "req": ChatMessage,
            "request": Request,
            "return": StreamingResponse,
        }
        router.add_api_route(
            routes.stream,
            chat_stream,
            methods=["POST"],
            response_model=None,
            summary="Stream one chat turn",
            operation_id="chat_stream",
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

    if routes.invoke is not None:

        async def chat_invoke(req: ChatMessage, request: Request) -> JSONResponse:
            payload = await assistant.invoke(
                req.message,
                req.session_id,
                request=await _turn_request(request),
            )
            return JSONResponse(payload)

        chat_invoke.__annotations__ = {
            "req": ChatMessage,
            "request": Request,
            "return": JSONResponse,
        }
        router.add_api_route(
            routes.invoke,
            chat_invoke,
            methods=["POST"],
            response_model=None,
            summary="Run one chat turn and return the reply",
            operation_id="chat_invoke",
        )

    if routes.history is not None:
        if "{session_id}" in routes.history:

            async def runs_path(session_id: str) -> Any:
                return await _history_response(
                    assistant, session_id, routes.history_messages
                )

            runs_path.__annotations__ = {"session_id": str, "return": Any}
            history_endpoint: Any = runs_path
        else:

            async def runs_query(session_id: str = "") -> Any:
                return await _history_response(
                    assistant, session_id, routes.history_messages
                )

            runs_query.__annotations__ = {"session_id": str, "return": Any}
            history_endpoint = runs_query
        router.add_api_route(
            routes.history,
            history_endpoint,
            methods=["GET"],
            response_model=None,
            summary="Read a chat session",
            operation_id="chat_history",
        )

    if routes.clear is not None:
        if "{session_id}" in routes.clear:

            async def clear_path(session_id: str) -> dict[str, bool]:
                await assistant.store.delete_session(session_id)
                return {"ok": True}

            clear_path.__annotations__ = {
                "session_id": str,
                "return": dict[str, bool],
            }
            clear_endpoint: Any = clear_path
        else:

            async def clear_query(session_id: str = "") -> dict[str, bool]:
                if session_id:
                    await assistant.store.delete_session(session_id)
                return {"ok": True}

            clear_query.__annotations__ = {
                "session_id": str,
                "return": dict[str, bool],
            }
            clear_endpoint = clear_query
        router.add_api_route(
            routes.clear,
            clear_endpoint,
            methods=[routes.clear_method],
            summary="Clear a chat session",
            operation_id="chat_clear",
        )

    return router


async def _history_response(
    assistant: Chat | ChatAssistant, session_id: str, messages_only: bool
) -> Any:
    from fastapi.responses import JSONResponse

    state = await assistant.history(session_id)
    if messages_only and isinstance(state, Mapping):
        return JSONResponse(state.get("messages", []))
    return JSONResponse(state)


def create_chat_app(
    chat: Chat,
    *,
    prefix: str = "/api",
    routes: ChatRoutes | Mapping[str, Any] | None = None,
    title: str = "reactifact chat",
    version: str = "0.1.0",
    description: str = "",
    traces: Any | None = None,
    contracts: bool = True,
    dependencies: Sequence[Any] = (),
    request_factory: Callable[[Any], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
    | None = None,
    validate: bool = True,
    gzip: bool = True,
    router_kwargs: Mapping[str, Any] | None = None,
    **fastapi_kwargs: Any,
) -> Any:
    """A ready FastAPI app around a session-persisted `Chat`.

    Mounts the chat router (SSE + history + clear), the contracts page and the
    trace dashboard. `routes=` selects the paths; the DEVOPS-style contract is:

        chat.http(routes=ChatRoutes(
            stream="/chat/stream",
            invoke="/chat",
            history="/chat/history",
            clear="/chat/clear",
            clear_method="POST",
            history_messages=True,
        ))

    `validate=True` fails at startup if the declared contracts are broken.
    """
    require_extra("web.create_chat_app", "fastapi", "web")
    from fastapi import FastAPI
    from fastapi.middleware.gzip import GZipMiddleware

    if validate:
        report = chat.validate()
        if not report.ok:
            raise RuntimeError("chat contracts are invalid\n\n" + report.render())

    user_lifespan = fastapi_kwargs.pop("lifespan", None)
    chat_lifespan = getattr(chat, "lifespan", None)
    lifespan = user_lifespan
    if chat_lifespan is not None:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _lifespan(app: Any) -> AsyncIterator[None]:
            async with chat:
                if user_lifespan is None:
                    yield
                else:
                    async with user_lifespan(app):
                        yield

        lifespan = _lifespan

    app = FastAPI(
        title=title,
        version=version,
        description=description,
        lifespan=lifespan,
        **fastapi_kwargs,
    )
    deps = list(dependencies)
    app.include_router(
        create_chat_router(
            chat,
            prefix=prefix,
            routes_override=routes,
            request_factory=request_factory,
            **(router_kwargs or {}),
        ),
        dependencies=deps,
    )

    if contracts:
        from ..contracts.web import create_contracts_router

        app.include_router(create_contracts_router(chat.describe), dependencies=deps)

    if traces is not False:
        from ..tracing.web import create_trace_router

        store = chat.trace_store
        if traces is not None and traces is not True:
            store = traces
        if traces is True and store is None:
            raise ValueError(
                "create_chat_app(traces=True) needs Chat(traces=True) "
                "or an explicit TraceStore/path"
            )
        if isinstance(store, (str, Path)):
            from ..tracing import TraceStore

            store = TraceStore(str(store))
        if store is not None:
            app.include_router(create_trace_router(store), dependencies=deps)

    install_error_handlers(app)
    if gzip:
        app.add_middleware(GZipMiddleware, minimum_size=1024)
    return app
