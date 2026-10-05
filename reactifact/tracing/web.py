"""FastAPI router for viewing traces (§54).

Mounted in the app: `app.include_router(create_trace_router(store))`.
Serves a JSON API (`/api/traces`, `/api/traces/{id}`, `/api/tags`), a `/traces`
list, and a trace page `/traces/{id}` (templates in `templates/`). Polling
provides "real time".

Beyond reading, the router is the **review surface**: a reviewer attaches tags
(+ notes) to a run from the UI, so runs can later be found by tag and acted on
(prompt/behaviour fixes). Assignments are persisted by the store, not the
runtime.

The router works against any `TraceStoreProtocol` — SQLite (`TraceStore`) or
Postgres (`PostgresStore`) — both have async `query`/`get` and the annotation
methods.

FastAPI is imported lazily inside `create_trace_router`: the module itself and the whole
`tracing` package do not require fastapi installed — it is needed only where
the router is created (a web app).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from .columns import TraceColumn
from .store import TraceStoreProtocol

if TYPE_CHECKING:
    from fastapi import APIRouter, Request

_TEMPLATES = Path(__file__).parent / "templates"


def _mount_base(request: Request, logical_path: str) -> str:
    """Path prefix this router is mounted under — ``include_router(prefix=…)``
    and/or the ASGI ``root_path`` — so templates can emit prefix-aware
    absolute URLs (``/*`` links and ``fetch`` paths) instead of assuming the
    service sits at the origin root.

    The prefix is everything before the *last* occurrence of this route's first
    logical segment (``/traces`` / ``/sessions``) in the request path. The last
    occurrence — not the first — because the mount prefix may itself contain
    that segment (``prefix="/api/traces"`` → ``/api/traces/traces``), while the
    route's own segment is always the one nearest the path parameters.
    """
    root_path = request.scope.get("root_path", "") or ""
    marker = "/" + logical_path.strip("/").split("/")[0]
    index = request.url.path.rfind(marker)
    return root_path + (request.url.path[:index] if index > 0 else "")


def _render_page(
    request: Request, filename: str, logical_path: str, **replacements: str
) -> str:
    """Read a UI template and fill ``__BASE__`` (mount prefix) plus any
    caller placeholders (e.g. ``__RUN_ID__``)."""
    text = (_TEMPLATES / filename).read_text(encoding="utf-8")
    text = text.replace("__BASE__", _mount_base(request, logical_path))
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


class TagAssign(BaseModel):
    tag: str = Field(min_length=1, max_length=64)
    note: str = ""
    color: str = ""


class BulkTag(BaseModel):
    run_ids: list[str] = Field(min_length=1)
    tag: str = Field(min_length=1, max_length=64)
    note: str = ""
    color: str = ""


class BulkUntag(BaseModel):
    run_ids: list[str] = Field(min_length=1)
    tag: str = Field(min_length=1, max_length=64)


class TagUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: str = ""


class TagPatch(BaseModel):
    new_name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = None


def create_trace_router(
    store: TraceStoreProtocol,
    *,
    columns: Sequence[TraceColumn] | None = None,
    username: str | None = None,
    password: str | None = None,
    export_limit: int = 500,
) -> APIRouter:
    """Router over a trace store (SQLite or Postgres).

    `columns` adds configurable, artifact-derived columns to the traces table
    (see `TraceColumn`): each names an agent/artifact type/field, computed
    server-side per run and shown in the UI (which can hide/reorder them).

    Returns `fastapi.APIRouter`; fastapi is imported here (lazily)
    so that `reactifact.tracing.web` works without it.

    If `username`/`password` are set — all handlers (including UI pages)
    are protected with HTTP Basic auth. Traces contain full prompts and
    artifact contents — do not expose them without auth.
    """
    import secrets

    from .._extras import require_extra

    require_extra("tracing.web.create_trace_router", "fastapi", "web")

    from fastapi import APIRouter, Depends, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    # `from __future__ import annotations` keeps every hint a string, resolved
    # against this module's globals at decoration time; fastapi is imported
    # lazily here, so publish the names the page handlers annotate with.
    globals()["Request"] = Request
    from fastapi.security import HTTPBasic, HTTPBasicCredentials

    dependencies = []
    if username is not None and password is not None:
        security = HTTPBasic(auto_error=False)
        expected_user = username
        expected_pass = password

        def _check(
            credentials: HTTPBasicCredentials | None = Depends(  # noqa: B008
                security
            ),
        ) -> None:
            if credentials is None or not (
                secrets.compare_digest(credentials.username, expected_user)
                and secrets.compare_digest(credentials.password, expected_pass)
            ):
                raise HTTPException(
                    status_code=401,
                    detail="Unauthorized",
                    headers={"WWW-Authenticate": "Basic"},
                )

        dependencies = [Depends(_check)]

    router = APIRouter(dependencies=dependencies)

    # ---- reads ------------------------------------------------------------- #

    @router.get("/api/traces")
    async def list_traces(
        session_id: str | None = Query(default=None),
        outcome: str | None = Query(default=None),
        tag: list[str] | None = Query(default=None),  # noqa: B008
        tag_mode: str = Query(default="any", pattern="^(any|all)$"),
        q: str | None = Query(default=None),
        sort: str = Query(default="started_at"),
        order: str = Query(default="desc", pattern="^(asc|desc)$"),
        limit: int = Query(default=25, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return await store.query(
            session_id=session_id,
            outcome=outcome,
            tags=tag,
            tag_mode=tag_mode,
            q=q,
            sort=sort,
            order=order,
            columns=columns,
            limit=limit,
            offset=offset,
        )

    # ``response_model=None`` is required: the module uses ``from __future__
    # import annotations`` and imports fastapi lazily here, so the ``->
    # JSONResponse`` return annotation stays an unresolvable forward ref and
    # FastAPI would fail to build ``/openapi.json``.
    @router.get("/api/traces/export", response_model=None)
    async def export_traces(
        session_id: str | None = Query(default=None),
        outcome: str | None = Query(default=None),
        tag: list[str] | None = Query(default=None),  # noqa: B008
        tag_mode: str = Query(default="any", pattern="^(any|all)$"),
        q: str | None = Query(default=None),
        sort: str = Query(default="started_at"),
        order: str = Query(default="desc", pattern="^(asc|desc)$"),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> JSONResponse:
        """Full traces (spans, LLM calls, annotations) for the current filter."""
        listed = await store.query(
            session_id=session_id,
            outcome=outcome,
            tags=tag,
            tag_mode=tag_mode,
            q=q,
            sort=sort,
            order=order,
            limit=min(limit, export_limit),
            offset=0,
        )
        items: list[dict[str, Any]] = []
        for row in listed["items"]:
            trace = await store.get(row["id"])
            if trace is not None:
                items.append(trace.to_dict())
        payload = {
            "exported_at": datetime.now(UTC).isoformat(),
            "filters": {
                "session_id": session_id,
                "outcome": outcome,
                "tags": tag or [],
                "tag_mode": tag_mode,
                "q": q,
            },
            "count": len(items),
            "items": items,
        }
        return JSONResponse(
            content=payload,
            headers={
                "Content-Disposition": 'attachment; filename="reactifact-traces.json"'
            },
        )

    @router.get("/api/sessions")
    async def list_sessions(
        q: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return await store.sessions(q=q, limit=limit, offset=offset)

    @router.get("/api/columns")
    async def list_columns() -> dict[str, Any]:
        items = [{"label": column.label} for column in (columns or [])]
        return {"items": items, "total": len(items)}

    @router.get("/api/traces/{trace_id}")
    async def get_trace(trace_id: str) -> dict[str, Any]:
        trace = await store.get(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="trace not found")
        data = trace.to_dict()
        if columns:
            data["fields"] = await store.field_values(trace_id, columns)
        return data

    # ---- annotation writes ------------------------------------------------- #

    @router.post("/api/traces/tags")
    async def bulk_tag(body: BulkTag) -> dict[str, Any]:
        count = await store.tag_runs(
            body.run_ids, body.tag, note=body.note, color=body.color
        )
        return {"tagged": count}

    @router.post("/api/traces/tags/remove")
    async def bulk_untag(body: BulkUntag) -> dict[str, Any]:
        count = await store.untag_runs(body.run_ids, body.tag)
        return {"untagged": count}

    @router.post("/api/traces/{trace_id}/tags")
    async def assign_tag(trace_id: str, body: TagAssign) -> dict[str, Any]:
        count = await store.tag_runs(
            [trace_id], body.tag, note=body.note, color=body.color
        )
        if count == 0:
            raise HTTPException(status_code=404, detail="trace not found")
        return {"tagged": count}

    @router.delete("/api/traces/{trace_id}/tags/{tag}")
    async def remove_tag(trace_id: str, tag: str) -> dict[str, Any]:
        count = await store.untag_runs([trace_id], tag)
        return {"untagged": count}

    # ---- tag vocabulary ---------------------------------------------------- #

    @router.get("/api/tags")
    async def list_tags() -> dict[str, Any]:
        items = await store.list_tags()
        return {"items": items, "total": len(items)}

    @router.post("/api/tags")
    async def create_tag(body: TagUpsert) -> dict[str, Any]:
        await store.set_tag_color(body.name, body.color)
        return {"ok": True}

    @router.patch("/api/tags/{name}")
    async def patch_tag(name: str, body: TagPatch) -> dict[str, Any]:
        if body.color is not None:
            await store.set_tag_color(name, body.color)
        if body.new_name is not None and body.new_name != name:
            await store.rename_tag(name, body.new_name)
        return {"ok": True}

    @router.delete("/api/tags/{name}")
    async def delete_tag(name: str) -> dict[str, Any]:
        await store.delete_tag(name)
        return {"ok": True}

    # ---- pages ------------------------------------------------------------- #

    @router.get("/traces/assets/app.css", include_in_schema=False)
    async def app_css() -> Response:
        return Response(
            (_TEMPLATES / "app.css").read_text(encoding="utf-8"),
            media_type="text/css",
        )

    @router.get("/traces", response_class=HTMLResponse)
    async def traces_list_page(request: Request) -> str:
        return _render_page(request, "ui.html", "/traces")

    @router.get("/sessions", response_class=HTMLResponse)
    async def sessions_list_page(request: Request) -> str:
        return _render_page(request, "sessions.html", "/sessions")

    @router.get("/traces/{trace_id}", response_class=HTMLResponse)
    async def traces_run_page(request: Request, trace_id: str) -> str:
        from ..viz import trace_provenance_to_mermaid, trace_to_mermaid

        trace = await store.get(trace_id)
        mermaid = trace_to_mermaid(trace) if trace is not None else ""
        provenance = trace_provenance_to_mermaid(trace) if trace is not None else ""
        return _render_page(
            request,
            "ui_run.html",
            "/traces/{trace_id}",
            __RUN_ID__=json.dumps(trace_id),
            __MERMAID__=json.dumps(mermaid),
            __MERMAID_GRAPH__=json.dumps(provenance),
        )

    return router
