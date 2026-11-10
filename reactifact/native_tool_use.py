"""Compatibility import for native tool helpers (canonical: `reactifact.tools.native`)."""

from .tools.native import (
    ToolCall,
    looks_like_tool_markup,
    native_complete,
    parse_textual_tool_calls,
    parse_tool_calls,
    parse_tool_calls_with_textual,
    tools_payload,
)

__all__ = [
    "ToolCall",
    "looks_like_tool_markup",
    "native_complete",
    "parse_textual_tool_calls",
    "parse_tool_calls",
    "parse_tool_calls_with_textual",
    "tools_payload",
]
