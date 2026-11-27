"""MCP server: exposes reactifact `Tool`s as MCP tools, and a `Context`'s
artifacts as MCP resources — so any MCP client (Claude Desktop, Claude Code,
another agent) can call into a running reactifact app. Requires the `mcp`
extra.

Tool schemas come from `Tool.schema` (already a JSON schema — every `Tool`
has one, hand-written or `@tool`-derived). The `mcp` SDK's own `add_tool`
only accepts a plain Python function and derives the schema from its
*signature*, so each `Tool` gets a `**kwargs` wrapper with a generated
`__signature__` — the caller sees real argument names and types, not a blind
bag of kwargs, and no schema-derived source is executed.
"""

from __future__ import annotations

import inspect
import keyword
from collections.abc import Callable, Sequence
from typing import Any

from .._extras import require_extra
from ..context import Context
from ..tools import Tool

_JSON_TYPE_TO_PY: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def create_mcp_server(
    tools: Sequence[Tool] = (),
    *,
    context: Context | None = None,
    name: str = "reactifact",
    instructions: str = "",
) -> Any:
    """Builds an `mcp.server.mcpserver.MCPServer` exposing `tools`.

    With `context=`, two read-only resources are added so an MCP client can
    inspect a running Context: `context://artifacts/{artifact_type}` (list,
    e.g. `context://artifacts/Answer`) and `context://artifact/{artifact_id}`
    (one artifact, its current data and version).

    Run it with `server.run_stdio_async()`, or mount `server.streamable_http_app()`
    on a FastAPI app alongside `create_trace_router` / `create_chat_router`.
    """
    require_extra("create_mcp_server", "mcp", "mcp")
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name=name, instructions=instructions or None)
    for t in tools:
        _register_tool(server, t)
    if context is not None:
        _register_context_resources(server, context)
    return server


def _register_tool(server: Any, t: Tool) -> None:
    from mcp.types import ToolAnnotations

    fn = _bridge_function(t)
    annotations = ToolAnnotations(destructive_hint=True) if t.destructive else None
    server.add_tool(fn, name=t.name, description=t.description, annotations=annotations)


def _bridge_function(t: Tool) -> Callable[..., Any]:
    """Builds a signature-preserving wrapper for `t` without `exec`.

    `mcp` publishes a tool's parameters by introspecting a Python function.
    A `Tool` already carries a JSON schema, so the wrapper gets the schema's
    names/types as its `__signature__`; the body is a single `**kwargs` call
    into `Tool.execute`. JSON property names must be valid Python identifiers
    (and must not start with `_`, which the MCP SDK reserves).
    """
    properties = t.schema.get("properties", {}) or {}
    if not isinstance(properties, dict):
        raise ValueError(f"Tool {t.name!r}: schema['properties'] must be an object")
    required = set(t.schema.get("required", []) or [])
    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}

    for pname in properties:
        if (
            not isinstance(pname, str)
            or not pname.isidentifier()
            or keyword.iskeyword(pname)
            or pname.startswith("_")
        ):
            raise ValueError(
                f"Tool {t.name!r}: parameter {pname!r} is not a valid MCP "
                "parameter name (use an identifier, no leading underscore)"
            )
        prop = properties[pname] or {}
        json_type = prop.get("type") if isinstance(prop, dict) else None
        py_type = (
            _JSON_TYPE_TO_PY.get(json_type, Any) if isinstance(json_type, str) else Any
        )
        is_required = pname in required
        annotation = py_type if is_required else py_type | None
        parameters.append(
            inspect.Parameter(
                pname,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=inspect.Parameter.empty if is_required else None,
                annotation=annotation,
            )
        )
        annotations[pname] = annotation

    async def _call(args: dict[str, Any]) -> Any:
        from mcp.server.mcpserver.exceptions import ToolError

        # optional params default to None above; drop unset ones so `Tool`s
        # with real (non-None) defaults in their own schema see those instead
        args = {k: v for k, v in args.items() if v is not None or k in required}
        result = await t.execute(args)
        if result.error:
            # `ToolError`, not a bare exception: an anticipated `Tool` failure
            # reaches the MCP client as `is_error=True` with this message —
            # any other exception type is masked to "Error executing tool …".
            raise ToolError(result.error)
        return result.data or result.text

    async def _bridge(**kwargs: Any) -> Any:
        return await _call(kwargs)

    _bridge.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    _bridge.__annotations__ = annotations
    _bridge.__name__ = t.name
    _bridge.__doc__ = t.description
    return _bridge


def _register_context_resources(server: Any, context: Context) -> None:
    import json

    async def list_artifacts_resource(artifact_type: str) -> str:
        matches = sorted(
            (
                a
                for a in context.list_artifacts()
                if type(a.data).__name__ == artifact_type
            ),
            key=lambda a: a.created_at,
            reverse=True,
        )
        return json.dumps(
            [
                {
                    "id": a.id,
                    "type": artifact_type,
                    "version": a.version,
                    "data": a.data.model_dump(),
                }
                for a in matches
            ],
            default=str,
        )

    async def get_artifact_resource(artifact_id: str) -> str:
        artifact = context.get(artifact_id)
        if artifact is None:
            return json.dumps({"error": f"no artifact with id {artifact_id!r}"})
        return json.dumps(
            {
                "id": artifact.id,
                "type": type(artifact.data).__name__,
                "version": artifact.version,
                "data": artifact.data.model_dump(),
            },
            default=str,
        )

    server.resource(
        "context://artifacts/{artifact_type}",
        description="Lists artifacts of one type currently in the Context, newest first.",
    )(list_artifacts_resource)
    server.resource(
        "context://artifact/{artifact_id}",
        description="Reads one artifact by id: its type, version, and current data.",
    )(get_artifact_resource)
