"""Optional FastAPI status router for knowledge ingestion.

Trace pages describe agent runs; ingestion is a background job with a
different lifecycle. This router is deliberately separate from
`reactifact.tracing.web` and reads the `DocStore` run ledger:

    from fastapi import FastAPI
    from reactifact.knowledge.web import create_knowledge_router

    app = FastAPI()
    app.include_router(
        create_knowledge_router(docstore, index=pipeline.index),
        prefix="/knowledge",
    )

It serves a small read-only page plus `GET /api/knowledge/status`. FastAPI is
imported lazily, so the knowledge package stays importable without the `web`
extra.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .docstore import DocStore
from .indexes import Indexer

if TYPE_CHECKING:
    from fastapi import APIRouter, Request

_TEMPLATE = Path(__file__).parent / "templates" / "knowledge.html"


def create_knowledge_router(
    docstore: DocStore,
    *,
    index: Indexer | None = None,
) -> APIRouter:
    """Build a read-only router over one knowledge `DocStore`.

    `index` is optional: when given, the status page shows its backend type and
    point count (`MemoryIndex.count` property, `SqliteIndex.count()` /
    `QdrantIndex.count()` coroutines).
    """
    from .._extras import require_extra

    require_extra("knowledge.web.create_knowledge_router", "fastapi", "web")

    from fastapi import APIRouter, Request
    from fastapi.responses import HTMLResponse

    # `from __future__ import annotations` keeps handler hints as strings;
    # publish the lazily imported name so FastAPI can resolve `Request`.
    globals()["Request"] = Request

    router = APIRouter()

    async def _index_status() -> dict[str, Any]:
        if index is None:
            return {"type": None, "count": None}
        value: int | None = None
        count = getattr(index, "count", None)
        if callable(count):
            result = count()
            resolved = await result if inspect.isawaitable(result) else result
            value = int(cast(Any, resolved))
        elif isinstance(count, int):
            value = count
        return {"type": type(index).__name__, "count": value}

    async def _status() -> dict[str, Any]:
        return {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "index": await _index_status(),
            "sources": docstore.snapshot(),
        }

    @router.get("/api/knowledge/status")
    async def knowledge_status() -> dict[str, Any]:
        return await _status()

    @router.get("", response_class=HTMLResponse)
    @router.get("/", response_class=HTMLResponse)
    async def knowledge_page(request: Request) -> str:
        base = (request.scope.get("root_path", "") + request.url.path).rstrip("/")
        text = _TEMPLATE.read_text(encoding="utf-8")
        return text.replace("__BASE__", base)

    return router


__all__ = ["create_knowledge_router"]
