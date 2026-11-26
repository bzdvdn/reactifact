"""Tool results and trace records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ToolError(BaseModel):
    """Typed tool failure — the structured half of `ToolOutput.error`.

    `error` stays a plain string for backward compatibility; `error_info` lets
    a caller branch on `code`/`retryable` instead of parsing a message. A
    `ToolOutput.failed(ToolError(...))` sets both.
    """

    code: str = ""
    message: str = ""
    retryable: bool = False

    def __str__(self) -> str:
        return self.message or self.code or "tool failed"


class ToolRun(BaseModel):
    """One tool execution recorded for a trace span (`AgentSpan.tools`).

    `ToolUse`/`run_tool_loop` open a per-task run log via `begin_tool_runs()`;
    the runtime attaches it to the agent span. Args are deliberately not
    recorded (they may carry secrets); name/latency/status/size is enough to
    spot a slow or failing tool.
    """

    tool: str
    latency_ms: float = 0.0
    ok: bool = True
    error: str | None = None
    chars: int = 0
    #: Tool arguments, recorded only when `RuntimeResources(record_tool_args=
    #: True)` (and scrubbed through `resources.redactor` when configured).
    args: dict[str, Any] | None = None
    #: Wall-clock start, so exporters can place the call on a timeline even
    #: when tools ran in parallel.
    started_at: datetime | None = None
    #: The produce that executed the tool (empty for a direct call outside a
    #: produce), so the UI can group calls by produce.
    produce: str = ""
    #: The returned text, redacted/truncated by the trace policy (`None` on a
    #: failure or when output recording is disabled).
    output: str | None = None


class ToolOutput(BaseModel):
    """Tool execution result. `error` is set on failure (not an exception)."""

    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    #: Structured failure details (code/retryable). `error` mirrors the
    #: message when this is set, so existing string-based callers keep working.
    error_info: ToolError | None = None

    @classmethod
    def failed(cls, error: ToolError) -> ToolOutput:
        """Builds a failure result from a `ToolError`."""
        return cls(error=str(error), error_info=error)

    @property
    def ok(self) -> bool:
        return not (self.error or self.error_info)


__all__ = ["ToolError", "ToolOutput", "ToolRun"]
