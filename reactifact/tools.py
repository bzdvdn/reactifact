"""Tools: the \"function + schema\" contract for LLM agents (§46–47, §68).

`@tool` turns an async function into a `FunctionTool` (name, description and JSON
argument schema are derived from the signature). Tool usage — see `llm_agent.LLMAgent`:
the \"which tool and with which arguments\" decision loop is driven by the LLM, the
framework executes.

Tools are capabilities; they are not part of consume/produce: those bind artifacts.
"""

from __future__ import annotations

import inspect
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, overload

from pydantic import BaseModel, Field, create_model

from .redaction import Redactor


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


@dataclass
class _ToolRunLog:
    runs: list[ToolRun] = field(default_factory=list)
    record_args: bool = False
    redactor: Redactor | None = None
    truncate: int | None = 1500
    record_output: bool = True


_tool_run_log: ContextVar[_ToolRunLog | None] = ContextVar(
    "reactifact_tool_runs", default=None
)


_current_produce: ContextVar[str] = ContextVar("reactifact_current_produce", default="")


def set_current_produce(name: str) -> Token[str]:
    """Marks the produce currently executing (used to attribute tool runs)."""
    return _current_produce.set(name)


def reset_current_produce(token: Token[str]) -> None:
    _current_produce.reset(token)


def begin_tool_runs(
    *,
    record_args: bool = False,
    redactor: Redactor | None = None,
    truncate: int | None = 1500,
    record_output: bool = True,
) -> Token[Any]:
    """Opens the per-task tool-run log (see `ToolRun`)."""
    return _tool_run_log.set(
        _ToolRunLog(
            record_args=record_args,
            redactor=redactor,
            truncate=truncate,
            record_output=record_output,
        )
    )


def tool_runs() -> list[ToolRun]:
    """The tool runs recorded for the current task (empty if none)."""
    log = _tool_run_log.get()
    return list(log.runs) if log is not None else []


def end_tool_runs(token: Token[Any]) -> None:
    _tool_run_log.reset(token)


async def execute_tool(tool: Tool, args: dict[str, Any]) -> ToolOutput:
    """Executes `tool` and records the call in the current task's run log.

    Framework loops (`ToolUse`, `recipes.run_tool_loop`) use this instead of
    `tool.execute` so tool calls show up on the trace span. A direct
    `tool.execute` call is not traced.
    """
    started_at = datetime.now(UTC)
    started = time.monotonic()
    try:
        output = await tool.execute(args)
    except Exception as exc:
        _record_tool_run(
            tool.name,
            started_at=started_at,
            latency_ms=(time.monotonic() - started) * 1000,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            args=args,
        )
        raise
    if output.error:
        error = output.error
        chars = 0
    else:
        error = None
        chars = (
            len(output.text)
            if output.text
            else len(json.dumps(output.data, ensure_ascii=False))
        )
    _record_tool_run(
        tool.name,
        started_at=started_at,
        latency_ms=(time.monotonic() - started) * 1000,
        ok=output.ok,
        error=error,
        chars=chars,
        args=args,
        output_text=(
            output.text or json.dumps(output.data, ensure_ascii=False)
            if not output.error
            else None
        ),
    )
    return output


def _redact_args(value: Any, redactor: Redactor | None) -> Any:
    if redactor is None:
        return value
    if isinstance(value, str):
        return redactor.redact(value)
    if isinstance(value, dict):
        return {key: _redact_args(item, redactor) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_args(item, redactor) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_args(item, redactor) for item in value)
    return value


def _truncate_text(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    return f"{text[:limit]}\n…[truncated {len(text) - limit} chars]"


def _record_tool_run(
    tool: str,
    *,
    started_at: datetime,
    latency_ms: float,
    ok: bool,
    error: str | None = None,
    chars: int = 0,
    args: dict[str, Any] | None = None,
    output_text: str | None = None,
) -> None:
    log = _tool_run_log.get()
    if log is None:
        return
    recorded_args = _redact_args(args, log.redactor) if log.record_args else None
    recorded_output = None
    if log.record_output and output_text is not None:
        recorded_output = _truncate_text(
            str(_redact_args(output_text, log.redactor)), log.truncate
        )
    log.runs.append(
        ToolRun(
            tool=tool,
            latency_ms=latency_ms,
            ok=ok,
            error=error,
            chars=chars,
            args=recorded_args if isinstance(recorded_args, dict) else None,
            started_at=started_at,
            produce=_current_produce.get(),
            output=recorded_output,
        )
    )


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


class Tool(ABC):
    """Contract for an external operation: \"Do this operation\" (§46).

    For an LLM agent (`LLMAgent`) a tool must provide a JSON schema of its
    arguments (`schema`), from which the model picks the operation and arguments.
    `@tool` builds it from the signature automatically.
    """

    name: str
    description: str = ""
    destructive: bool = False
    schema: dict[str, Any] = {}

    @abstractmethod
    async def execute(self, args: dict[str, Any]) -> ToolOutput:
        """Execute the operation; failure is an exception or `ToolOutput(error=...)`."""
        ...


def _args_model(fn: Callable[..., Any]) -> type[BaseModel]:
    """Builds a Pydantic model of arguments from the function signature (→ JSON schema)."""
    fields: dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            continue
        annotation = (
            param.annotation if param.annotation is not inspect.Parameter.empty else Any
        )
        if param.default is inspect.Parameter.empty:
            fields[name] = (annotation, ...)  # required argument
        else:
            fields[name] = (annotation, param.default)
    return create_model(f"args_{fn.__name__}", **fields)


class FunctionTool(Tool):
    """Tool from a plain async function: schema is derived from the signature."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        destructive: bool = False,
        description: str | None = None,
    ):
        self._fn = fn
        self.args_model = _args_model(fn)
        self.name = name or fn.__name__
        self.destructive = destructive
        self.description = description or (fn.__doc__ or "").strip() or fn.__name__
        self.schema = self.args_model.model_json_schema()

    async def execute(self, args: dict[str, Any]) -> ToolOutput:
        validated = self.args_model(**args)
        result = await self._fn(**validated.model_dump())
        if isinstance(result, ToolOutput):
            return result
        if isinstance(result, str):
            return ToolOutput(text=result)
        if isinstance(result, dict):
            return ToolOutput(data=result)
        return ToolOutput(text=str(result))


@overload
def tool(
    fn: Callable[..., Any],
    *,
    name: str | None = None,
    destructive: bool = False,
    description: str | None = None,
) -> FunctionTool: ...


@overload
def tool(
    fn: None = None,
    *,
    name: str | None = None,
    destructive: bool = False,
    description: str | None = None,
) -> Callable[[Callable[..., Any]], FunctionTool]: ...


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    destructive: bool = False,
    description: str | None = None,
) -> Any:
    """Decorator: turns an async function into a `FunctionTool` (schema from the signature)."""

    def wrap(f: Callable[..., Any]) -> FunctionTool:
        return FunctionTool(
            f, name=name, destructive=destructive, description=description
        )

    return wrap(fn) if fn is not None else wrap
