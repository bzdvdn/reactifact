"""FastAPI app factory for the reactifact chat template.

Run with the factory form (no import-time env validation):

    uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from reactifact.tracing.web import create_trace_router

from app.api.routes.chat import router as chat_router
from app.bootstrap import (
    build_assistant_from_settings,
    build_session_store,
    build_trace_store,
)
from app.config import Settings, get_settings
from app.security import get_current_user


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_runtime()

    store = build_session_store(settings)
    trace_store = build_trace_store(settings)
    assistant = build_assistant_from_settings(settings, store, trace_store)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await store.backend.aclose()

    app = FastAPI(title="reactifact fastapi chat", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.trace_store = trace_store
    app.state.assistant = assistant

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(chat_router, prefix="/api")
    app.include_router(
        create_trace_router(trace_store),
        dependencies=[Depends(get_current_user)],
    )
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:create_app", factory=True, host="127.0.0.1", port=8000, reload=True)
