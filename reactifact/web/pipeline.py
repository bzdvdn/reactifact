"""FastAPI pipeline adapter: typed /run and /stream routes."""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .._extras import require_extra
from ..invocation import Invocation
from .common import (
    PipelineRoutes,
    _check_path,
    _routes,
    error_payload,
    install_error_handlers,
    install_request_id,
    sse,
)

if TYPE_CHECKING:
    from fastapi import APIRouter

    from ..invocation import InvocationStore
    from ..pipeline import PipelineRunner


def create_pipeline_router(
    pipeline: PipelineRunner,
    *,
    prefix: str = "",
    routes_override: PipelineRoutes | Mapping[str, str | None] | None = None,
    request_factory: Callable[[Any], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
    | None = None,
    max_iterations: int = 100,
    with_health: bool = True,
    invocations: InvocationStore | None = None,
) -> APIRouter:
    """A FastAPI `APIRouter` for a typed `PipelineRunner`.

    Routes (relative to `prefix`):
      POST /run      — one run; body is `seed_type`, response is `answer_type`
      POST /stream   — the same run as SSE progress events + a terminal `done`
      GET  /health   — liveness (opt-out via `with_health=False`)

    `routes=` overrides the paths, e.g. `PipelineRoutes(run="/chat",
    stream="/chat/stream")`; `None` omits a route so you can add your own.

    The endpoint annotations are the pipeline's `seed_type`/`answer_type`, so
    FastAPI derives request validation, response validation, OpenAPI and
    `/docs` from the same models the runtime uses. `request_factory(request)`
    builds the per-run `request=` mapping (tenant/user metadata) and may be
    async.
    """
    require_extra("web.create_pipeline_router", "fastapi", "web")
    from fastapi import APIRouter, Request, Response
    from fastapi.responses import JSONResponse, StreamingResponse

    routes = cast(
        PipelineRoutes, _routes(PipelineRoutes(), routes_override, PipelineRoutes)
    )
    if not with_health:
        routes = replace(routes, health=None)
    for name in ("run", "stream", "health"):
        _check_path(getattr(routes, name), f"pipeline {name}")

    seed_type = getattr(pipeline, "seed_type", None)
    answer_type = getattr(pipeline, "answer_type", None)
    if seed_type is None or answer_type is None:
        raise ValueError(
            "create_pipeline_router requires Pipeline(seed_type=..., answer_type=...)"
        )

    # `Request`/`Response` are referenced from endpoint annotations; publish
    # them lazily like `create_chat_router` does.
    globals().setdefault("Request", Request)
    globals().setdefault("Response", Response)
    globals().setdefault("StreamingResponse", StreamingResponse)

    router = APIRouter(prefix=prefix)

    async def _turn_request(request: Any) -> Mapping[str, Any] | None:
        if request_factory is None:
            return None
        value = request_factory(request)
        if inspect.isawaitable(value):
            value = await value
        return value

    if routes.health is not None:

        @router.get(routes.health)
        async def health() -> dict[str, bool]:
            return {"ok": True}

    async def _start_idempotent(
        request: Any, seed: Any
    ) -> tuple[str | None, Any | None]:
        """Claim the request's idempotency key; return (store key, replay)."""
        idem = request.headers.get("Idempotency-Key")
        if not idem:
            return None, None
        if invocations is None:
            return None, JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "idempotency_not_configured",
                        "message": (
                            "Idempotency-Key needs create_pipeline_router("
                            "invocations=...)"
                        ),
                    }
                },
            )
        store_key = f"{pipeline.name}:{idem}"
        claim_id = uuid.uuid4().hex
        request_hash = hashlib.sha256(
            json.dumps(seed.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        record = {
            "status": "running",
            "claim_id": claim_id,
            "pipeline": pipeline.name,
            "request_hash": request_hash,
        }
        claimed, existing = await invocations.claim_invocation(store_key, record)
        if not claimed:
            status = existing.get("status") if existing is not None else None
            if (
                status == "done"
                and existing is not None
                and "body" in existing
                and existing.get("request_hash") == request_hash
            ):
                headers = {"Idempotent-Replayed": "true"}
                if existing.get("trace_id"):
                    headers["X-Reactifact-Run-Id"] = str(existing["trace_id"])
                return None, JSONResponse(
                    status_code=int(existing.get("status_code", 200)),
                    content=existing["body"],
                    headers=headers,
                )
            if status == "done" and existing is not None:
                return None, JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "idempotency_key_reused",
                            "message": (
                                "this Idempotency-Key was used with a different request"
                            ),
                        }
                    },
                )
            if status == "running":
                return None, JSONResponse(
                    status_code=409,
                    content={
                        "error": {
                            "code": "invocation_running",
                            "message": "an invocation with this key is running",
                        }
                    },
                )
            await invocations.delete_invocation(store_key)
            claimed, _ = await invocations.claim_invocation(store_key, record)
            if not claimed:
                return None, JSONResponse(
                    status_code=409,
                    content={
                        "error": {
                            "code": "invocation_running",
                            "message": "an invocation with this key is running",
                        }
                    },
                )
        request.state.reactifact_claim_id = claim_id
        request.state.reactifact_request_hash = request_hash
        return store_key, None

    async def run(seed: Any, request: Any, response: Any) -> Any:
        store_key, replay = await _start_idempotent(request, seed)
        if replay is not None:
            return replay
        idem = (
            request.headers.get("Idempotency-Key") if invocations is not None else None
        )
        invocation = Invocation.new(idempotency_key=idem) if idem else None
        try:
            result = await pipeline.arun(
                seed,
                max_iterations=max_iterations,
                request=await _turn_request(request),
                invocation=invocation,
            )
        except Exception:
            if store_key is not None and invocations is not None:
                await invocations.finish_invocation(
                    store_key,
                    {"status": "failed"},
                    claim_id=str(request.state.reactifact_claim_id),
                )
            raise
        request.state.reactifact_run_id = result.trace_id
        if result.trace_id:
            response.headers["X-Reactifact-Run-Id"] = result.trace_id
        artifact = result.context.latest(answer_type)
        if artifact is None:
            from fastapi.responses import JSONResponse

            payload = error_payload(
                RuntimeError(
                    f"pipeline completed without a {answer_type.__name__} artifact"
                ),
                request,
                code="no_answer",
                trace_id=result.trace_id,
            )
            error = payload["error"]
            headers = {"X-Request-ID": error["request_id"]}
            if error["trace_id"]:
                headers["X-Reactifact-Run-Id"] = error["trace_id"]
            if store_key is not None and invocations is not None:
                await invocations.finish_invocation(
                    store_key,
                    {"status": "failed"},
                    claim_id=str(request.state.reactifact_claim_id),
                )
            return JSONResponse(status_code=500, content=payload, headers=headers)
        if store_key is not None and invocations is not None:
            await invocations.finish_invocation(
                store_key,
                {
                    "status": "done",
                    "claim_id": str(request.state.reactifact_claim_id),
                    "status_code": 200,
                    "body": artifact.data.model_dump(mode="json"),
                    "trace_id": result.trace_id,
                    "request_hash": str(
                        getattr(request.state, "reactifact_request_hash", "")
                    ),
                },
                claim_id=str(request.state.reactifact_claim_id),
            )
        return artifact.data

    run.__annotations__ = {
        "seed": seed_type,
        "request": Request,
        "response": Response,
        "return": answer_type,
    }
    if routes.run is not None:
        router.add_api_route(
            routes.run,
            run,
            methods=["POST"],
            response_model=answer_type,
            summary="Run the pipeline once",
            operation_id="run_pipeline",
        )

    async def stream(seed: Any, request: Any) -> Any:
        if request.headers.get("Idempotency-Key"):
            code = (
                "idempotency_not_supported"
                if invocations is not None
                else "idempotency_not_configured"
            )
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": code,
                        "message": (
                            "Idempotency-Key is supported on /run, not /stream"
                            if invocations is not None
                            else "Idempotency-Key needs create_pipeline_router("
                            "invocations=...)"
                        ),
                    }
                },
            )
        turn_request = await _turn_request(request)

        async def frames() -> AsyncIterator[str]:
            try:
                async for event in pipeline.astream(
                    seed,
                    max_iterations=max_iterations,
                    request=turn_request,
                ):
                    yield sse("progress", event.to_dict())
            except Exception as exc:  # noqa: BLE001 — stream the failure, don't drop it
                yield sse(
                    "error",
                    {"code": type(exc).__name__, "message": str(exc)},
                )
            yield sse("done", {})

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    stream.__annotations__ = {
        "seed": seed_type,
        "request": Request,
        "return": StreamingResponse,
    }
    if routes.stream is not None:
        router.add_api_route(
            routes.stream,
            stream,
            methods=["POST"],
            response_model=None,
            summary="Run the pipeline and stream progress events",
            operation_id="stream_pipeline",
            responses={
                200: {
                    "description": (
                        "Server-Sent Events: `progress`, then `done` (or `error`)."
                    ),
                    "content": {"text/event-stream": {"schema": {"type": "string"}}},
                }
            },
        )
    return router


def create_http_app(
    pipeline: PipelineRunner,
    *,
    prefix: str = "",
    routes: PipelineRoutes | Mapping[str, str | None] | None = None,
    title: str = "reactifact pipeline",
    version: str = "0.1.0",
    description: str = "",
    traces: Any | None = None,
    contracts: bool = True,
    dependencies: Sequence[Any] = (),
    request_factory: Callable[[Any], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
    | None = None,
    max_iterations: int = 100,
    validate: bool = True,
    gzip: bool = True,
    invocations: InvocationStore | None = None,
    **fastapi_kwargs: Any,
) -> Any:
    """A ready FastAPI app around a typed `PipelineRunner`.

    Mounts the pipeline router, the contracts page (unless `contracts=False`)
    and the trace dashboard when the pipeline has a trace store. `validate=True`
    fails at startup if `pipeline.validate()` finds a broken contract — the
    FastAPI-style fail-fast check, but for artifact topology.

    `traces=True` requires `Pipeline(traces=True)` (or pass a `TraceStore`/path
    explicitly); otherwise there is no store to mount. `dependencies=` applies
    to every router (auth, tenancy, etc.).
    """
    require_extra("web.create_http_app", "fastapi", "web")
    from fastapi import FastAPI
    from fastapi.middleware.gzip import GZipMiddleware

    if (
        getattr(pipeline, "seed_type", None) is None
        or getattr(pipeline, "answer_type", None) is None
    ):
        raise ValueError(
            "create_http_app requires Pipeline(seed_type=..., answer_type=...)"
        )

    if validate:
        report = pipeline.validate()
        if not report.ok:
            raise RuntimeError("pipeline contracts are invalid\n\n" + report.render())

    user_lifespan = fastapi_kwargs.pop("lifespan", None)
    pipeline_lifespan = getattr(pipeline, "lifespan", None)
    lifespan = user_lifespan
    if pipeline_lifespan is not None:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _lifespan(app: Any) -> AsyncIterator[None]:
            async with pipeline:
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
    install_request_id(app)
    deps = list(dependencies)
    app.include_router(
        create_pipeline_router(
            pipeline,
            prefix=prefix,
            routes_override=routes,
            request_factory=request_factory,
            max_iterations=max_iterations,
            invocations=invocations,
        ),
        dependencies=deps,
    )

    if contracts:
        from ..contracts.web import create_contracts_router

        # `Pipeline.describe()` includes the seed/answer JSON Schemas; passing
        # the callable keeps the page live for dynamic pipelines.
        app.include_router(
            create_contracts_router(pipeline.describe), dependencies=deps
        )

    if traces is not False:
        from ..tracing.web import create_trace_router

        store = getattr(pipeline, "trace_store", None)
        if traces is not None and traces is not True:
            store = traces
        if traces is True and store is None:
            raise ValueError(
                "create_http_app(traces=True) needs RunConfig(traces=True) "
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
