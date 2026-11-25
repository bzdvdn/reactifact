"""reactifact.web — FastAPI adapters over chat and pipeline.

The package is the lazy boundary between the runtime and HTTP:

- `create_chat_router` / `create_chat_app` — sessions, history, SSE;
- `create_pipeline_router` / `create_http_app` — typed, stateless runs;
- `ChatRoutes` / `PipelineRoutes` — path overrides;
- `sse` / `install_error_handlers` — shared transport helpers.

FastAPI is imported only when a factory is called, so the core package stays
free of the `web` extra.
"""

from __future__ import annotations

from .chat import create_chat_app, create_chat_router
from .common import (
    DEFAULT_EVENT_NAMES,
    ChatMessage,
    ChatRoutes,
    PipelineRoutes,
    install_error_handlers,
    sse,
)
from .pipeline import create_http_app, create_pipeline_router

__all__ = [
    "DEFAULT_EVENT_NAMES",
    "ChatMessage",
    "ChatRoutes",
    "PipelineRoutes",
    "create_chat_app",
    "create_chat_router",
    "create_http_app",
    "create_pipeline_router",
    "install_error_handlers",
    "sse",
]
