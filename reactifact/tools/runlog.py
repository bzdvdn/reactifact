"""Per-task tool-run logging, instrumentation and the `execute_tool` entry point."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from ..redaction import Redactor
from .contracts import ToolOutput, ToolRun

if TYPE_CHECKING:
    from ..resources import RuntimeResources
    from .base import Tool


#: The resources bound for a tool call outside a runtime step
#: (`execute_tool(..., resources=...)`).
_bound_resources: ContextVar[RuntimeResources | None] = ContextVar(
    "reactifact_tool_resources", default=None
)


@dataclass
class _ToolRunLog:
    runs: list[ToolRun] = field(default_factory=list)
    record_args: bool = False
    redactor: Redactor | None = None
    truncate: int | None = 1500
    record_output: bool = True
    on_start: Callable[[str, dict[str, Any] | None], None] | None = None
    on_end: Callable[[ToolRun], None] | None = None
    #: Optional record/replay wrapper for `execute_tool` (LLM-style replay;
    #: see `reactifact.replay.RecordingToolRecorder`/`ReplayToolRecorder`).
    recorder: Any | None = None


_tool_run_log: ContextVar[_ToolRunLog | None] = ContextVar(
    "reactifact_tool_runs", default=None
)
_current_produce: ContextVar[str] = ContextVar("reactifact_current_produce", default="")


def set_current_produce(name: str) -> Token[str]:
    """Marks the produce currently executing (used to attribute tool runs)."""
    return _current_produce.set(name)


def reset_current_produce(token: Token[str]) -> None:
    _current_produce.reset(token)


def current_produce_name() -> str:
    """The produce/step currently executing (empty outside a step run)."""
    return _current_produce.get()


def begin_tool_runs(
    *,
    record_args: bool = False,
    redactor: Redactor | None = None,
    truncate: int | None = 1500,
    record_output: bool = True,
    on_start: Callable[[str, dict[str, Any] | None], None] | None = None,
    on_end: Callable[[ToolRun], None] | None = None,
    recorder: Any | None = None,
) -> Token[Any]:
    """Opens the per-task tool-run log (see `ToolRun`).

    `on_start`/`on_end` are optional, best-effort lifecycle observers (the
    runtime uses them to feed a `Tracer`); they must be sync and must not
    block. `on_start` receives redacted args only when `record_args=True`.
    """
    return _tool_run_log.set(
        _ToolRunLog(
            record_args=record_args,
            redactor=redactor,
            truncate=truncate,
            record_output=record_output,
            on_start=on_start,
            on_end=on_end,
            recorder=recorder,
        )
    )


def tool_runs() -> list[ToolRun]:
    """The tool runs recorded for the current task (empty if none)."""
    log = _tool_run_log.get()
    return list(log.runs) if log is not None else []


def end_tool_runs(token: Token[Any]) -> None:
    _tool_run_log.reset(token)


async def _execute_instrumented(
    tool: Tool,
    raw_execute: Callable[[dict[str, Any]], Any],
    args: dict[str, Any],
) -> ToolOutput:
    """The single instrumentation point for a tool execution.

    Used by the `Tool.execute` wrapper (both direct calls and `execute_tool`)
    and by `execute_tool` as a fallback for duck-typed tools that do not inherit
    `Tool`. It owns the `ToolRun`/trace hooks and the optional record/replay.
    """
    started_at = datetime.now(UTC)
    started = time.monotonic()
    log = _tool_run_log.get()
    if log is not None and log.on_start is not None:
        preview = _redact_args(args, log.redactor) if log.record_args else None
        with contextlib.suppress(Exception):  # best-effort observer
            log.on_start(tool.name, preview if isinstance(preview, dict) else None)
    retry = getattr(tool, "retry", None)
    timeout = getattr(tool, "timeout", None)

    async def attempt() -> ToolOutput:
        if log is not None and log.recorder is not None:
            return cast("ToolOutput", await log.recorder.execute(tool, args))
        if timeout is not None:
            async with asyncio.timeout(timeout):
                return cast("ToolOutput", await raw_execute(args))
        return cast("ToolOutput", await raw_execute(args))

    attempt_no = 0
    while True:
        attempt_no += 1
        try:
            output = await attempt()
        except Exception as exc:
            if (
                retry is None
                or attempt_no >= retry.max_attempts
                or not retry.should_retry(exc)
            ):
                run = _record_tool_run(
                    tool.name,
                    started_at=started_at,
                    latency_ms=(time.monotonic() - started) * 1000,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    args=args,
                )
                _notify_tool_end(log, run)
                raise
            await asyncio.sleep(retry.delay(attempt_no))
            continue
        if (
            retry is not None
            and output.error_info is not None
            and output.error_info.retryable
            and attempt_no < retry.max_attempts
        ):
            await asyncio.sleep(retry.delay(attempt_no))
            continue
        break
    if not output.ok:
        error = output.error or str(output.error_info or "tool failed")
        chars = 0
    else:
        error = None
        chars = (
            len(output.text)
            if output.text
            else len(json.dumps(output.data, ensure_ascii=False))
        )
    run = _record_tool_run(
        tool.name,
        started_at=started_at,
        latency_ms=(time.monotonic() - started) * 1000,
        ok=output.ok,
        error=error,
        chars=chars,
        args=args,
        output_text=(
            output.text or json.dumps(output.data, ensure_ascii=False)
            if output.ok
            else None
        ),
    )
    _notify_tool_end(log, run)
    return output


async def execute_tool(
    tool: Tool,
    args: dict[str, Any],
    *,
    resources: RuntimeResources | None = None,
) -> ToolOutput:
    """Executes a tool with the same trace/record instrumentation as `tool.execute`.

    Kept as the compatibility entry point for framework loops (`ToolUse`,
    `recipes.run_tool_loop`). Instrumentation lives on the `Tool` subclass
    wrapper, so a direct `tool.execute(...)` inside a step is traced too; only
    duck-typed tools that do not inherit `Tool` go through the fallback here.

    `resources=` supplies collaborators to a `@tool` that injects them when the
    call is not already inside a step (outside a runtime, tests, a web job).
    """
    token = _bound_resources.set(resources) if resources is not None else None
    try:
        if getattr(type(tool), "_reactifact_instrumented", False):
            return await tool.execute(args)
        return await _execute_instrumented(tool, tool.execute, args)
    finally:
        if token is not None:
            _bound_resources.reset(token)


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


def _notify_tool_end(log: _ToolRunLog | None, run: ToolRun | None) -> None:
    """Delivers a finished `ToolRun` to the optional observer (best-effort)."""
    if log is None or log.on_end is None or run is None:
        return
    with contextlib.suppress(Exception):  # best-effort observer
        log.on_end(run)


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
) -> ToolRun | None:
    log = _tool_run_log.get()
    if log is None:
        return None
    recorded_args = _redact_args(args, log.redactor) if log.record_args else None
    recorded_output = None
    if log.record_output and output_text is not None:
        recorded_output = _truncate_text(
            str(_redact_args(output_text, log.redactor)), log.truncate
        )
    run = ToolRun(
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
    log.runs.append(run)
    return run


__all__ = [
    "_bound_resources",
    "_execute_instrumented",
    "begin_tool_runs",
    "current_produce_name",
    "end_tool_runs",
    "execute_tool",
    "reset_current_produce",
    "set_current_produce",
    "tool_runs",
]
