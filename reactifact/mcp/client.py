"""MCP client: turns tools exposed by an external MCP server into `Tool`s, so
`ToolUse`/`ToolUseHITL`/`LLMAgent` call remote MCP tools the same way they
call local ones — no separate code path for "MCP tool" vs. `@tool`. Requires
the `mcp` extra.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from .._extras import require_extra
from ..tools import Tool, ToolOutput


class MCPTool(Tool):
    """One tool from a connected `mcp.ClientSession`, wrapped as a `Tool`."""

    def __init__(
        self,
        session: Any,
        *,
        name: str,
        description: str,
        schema: dict[str, Any],
        destructive: bool = False,
    ):
        self._session = session
        self.name = name
        self.description = description
        self.schema = schema
        self.destructive = destructive

    async def execute(self, args: dict[str, Any]) -> ToolOutput:
        result = await self._session.call_tool(self.name, args)
        return _to_tool_output(result)


def _to_tool_output(result: Any) -> ToolOutput:
    texts = [block.text for block in result.content if getattr(block, "text", None)]
    text = "\n".join(texts)
    if result.is_error:
        return ToolOutput(text=text, error=text or "MCP tool call failed")
    data = dict(result.structured_content) if result.structured_content else {}
    return ToolOutput(text=text, data=data)


async def mcp_tools(session: Any) -> list[Tool]:
    """Lists tools on a connected, initialized `mcp.ClientSession` and wraps each as a `Tool`."""
    listed = await session.list_tools()
    return [
        MCPTool(
            session,
            name=t.name,
            description=t.description or t.name,
            schema=t.input_schema,
            destructive=bool(t.annotations and t.annotations.destructive_hint),
        )
        for t in listed.tools
    ]


@asynccontextmanager
async def mcp_stdio_tools(
    command: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
) -> AsyncGenerator[list[Tool], None]:
    """Spawns an MCP server over stdio and yields its tools as `Tool`s.

    Example: `mcp_stdio_tools("npx", ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"])`.
    The connection stays open for the `async with` block; tools called after
    it exits will fail.
    """
    require_extra("mcp_stdio_tools", "mcp", "mcp")
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from mcp import ClientSession

    params = StdioServerParameters(command=command, args=args or [], env=env)
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield await mcp_tools(session)


@asynccontextmanager
async def mcp_http_tools(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    auth: Any = None,
) -> AsyncGenerator[list[Tool], None]:
    """Connects to an MCP server over streamable HTTP and yields its tools as
    `Tool`s.

    Pass `headers` for servers that require a static credential (e.g.
    `{"Authorization": "Bearer ..."}`), or `auth` (an `httpx2.Auth`) for a
    server that requires OAuth — see `reactifact.mcp.oauth_client_credentials`
    for the machine-to-machine case. The SDK's `streamable_http_client` has
    no `headers=`/`auth=` kwarg of its own; the documented way is a
    pre-configured client, which this builds (and owns/closes) with the same
    recommended timeouts the SDK's own default client uses (30s
    connect/write/pool, 300s read — a server may hold a response stream
    open). Note this must be `httpx2.AsyncClient` (the MCP SDK's own httpx
    fork/dependency, not plain `httpx` — `streamable_http_client` rejects
    the wrong one at the type level), imported lazily here since it's only
    guaranteed installed alongside the `mcp` extra.
    """
    require_extra("mcp_http_tools", "mcp", "mcp")
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    from mcp import ClientSession

    async with AsyncExitStack() as stack:
        http_client = None
        if headers is not None or auth is not None:
            http_client = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers=headers, auth=auth, timeout=httpx2.Timeout(30.0, read=300.0)
                )
            )
        read, write = await stack.enter_async_context(
            streamable_http_client(url, http_client=http_client)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        yield await mcp_tools(session)
