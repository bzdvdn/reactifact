"""Tools: contracts, execution logging and LLM tool-use loops."""

from .base import FunctionTool, Tool, tool
from .contracts import ToolError, ToolOutput, ToolRun
from .runlog import (
    begin_tool_runs,
    current_produce_name,
    end_tool_runs,
    execute_tool,
    reset_current_produce,
    set_current_produce,
    tool_runs,
)

__all__ = [
    "FunctionTool",
    "Tool",
    "ToolError",
    "ToolOutput",
    "ToolRun",
    "begin_tool_runs",
    "current_produce_name",
    "end_tool_runs",
    "execute_tool",
    "reset_current_produce",
    "set_current_produce",
    "tool",
    "tool_runs",
]
