"""MCP integration: talk to external MCP servers, or serve reactifact `Tool`s
and `Context` artifacts as one. Requires the `mcp` extra — nothing here is
imported by reactifact's core, so a bare `pip install reactifact` never pulls
in the `mcp` SDK.
"""

from __future__ import annotations

from .client import MCPTool, mcp_http_tools, mcp_stdio_tools, mcp_tools
from .oauth import InMemoryTokenStorage, oauth_client_credentials
from .server import create_mcp_server

__all__ = [
    "InMemoryTokenStorage",
    "MCPTool",
    "create_mcp_server",
    "mcp_http_tools",
    "mcp_stdio_tools",
    "mcp_tools",
    "oauth_client_credentials",
]
