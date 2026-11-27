"""FastAPI adapter for the static contracts page (JSON/Markdown/HTML)."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from .._extras import require_extra
from .report import ContractReport, describe_contracts

if TYPE_CHECKING:
    from fastapi import APIRouter


def create_contracts_router(
    agents: (
        Sequence[Any] | ContractReport | Callable[[], Sequence[Any] | ContractReport]
    )
    | None = None,
    *,
    groups: (
        Sequence[Any] | ContractReport | Callable[[], Sequence[Any] | ContractReport]
    )
    | None = None,
    prefix: str = "",
    title: str = "reactifact contracts",
    username: str | None = None,
    password: str | None = None,
) -> APIRouter:
    """Serve the static agent contracts as a FastAPI page + JSON + Markdown.

    `agents` is a sequence of `Agent`s, a ready `ContractReport` (e.g.
    `Pipeline.describe()`), or a zero-arg callable returning either — for
    dynamic registries. Routes (relative to `prefix`): `/contracts` (HTML),
    `/api/contracts` (JSON, including seed/answer schemas when the report has
    them), `/contracts.md` (Markdown).

    This is opt-in and not mounted by default — tool descriptions and schemas
    can be sensitive. Pass `username`/`password` for HTTP Basic auth, the same
    way as the trace dashboard. `groups=` is a compatibility alias for
    `agents=`.
    """
    if agents is not None and groups is not None:
        raise TypeError("Pass only one of agents= or groups=")
    source = agents if agents is not None else groups
    if source is None:
        raise TypeError("create_contracts_router requires agents=")
    require_extra("contracts.web.create_contracts_router", "fastapi", "web")
    from fastapi import APIRouter, Depends, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    # Endpoint annotations are strings (`from __future__ import annotations`);
    # publish the response classes so FastAPI/OpenAPI can resolve them.
    globals().setdefault("HTMLResponse", HTMLResponse)
    globals().setdefault("JSONResponse", JSONResponse)
    globals().setdefault("Response", Response)

    def _report() -> ContractReport:
        value = source() if callable(source) else source
        if isinstance(value, ContractReport):
            return value
        return describe_contracts(value)

    dependencies = []
    if username is not None and password is not None:
        from fastapi.security import HTTPBasic, HTTPBasicCredentials

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

        dependencies = [Depends(_check)]  # noqa: B008

    router = APIRouter(prefix=prefix, dependencies=dependencies)

    @router.get("/contracts", response_class=HTMLResponse)
    async def contracts_page() -> str:
        return _report().to_html(title=title)

    @router.get("/api/contracts")
    async def contracts_json() -> JSONResponse:
        return JSONResponse(_report().to_dict())

    @router.get("/contracts.md")
    async def contracts_markdown() -> Response:
        return Response(
            _report().to_markdown(),
            media_type="text/markdown",
        )

    return router


__all__ = ["create_contracts_router"]
