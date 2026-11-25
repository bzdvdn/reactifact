"""Tools: the \"function + schema\" contract for LLM agents (§46–47, §68).

`@tool` turns an async function into a `FunctionTool` (name, description and JSON
argument schema are derived from the signature). Tool usage — see `llm_agent.LLMAgent`:
the \"which tool and with which arguments\" decision loop is driven by the LLM, the
framework executes.

Tools are capabilities; they are not part of consume/produce: those bind artifacts.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import inspect
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)
from uuid import UUID

from pydantic import BaseModel, Field, create_model

from ..di import Inject
from ..policy import RetryPolicy
from ..redaction import Redactor

if TYPE_CHECKING:
    from ..context import Context
    from ..resources import RuntimeResources

#: Annotations that are data, never an injected collaborator. Everything else
#: that is a plain class (a client, a protocol implementation) is resolved from
#: `RuntimeResources` and excluded from the JSON schema.
_DATA_ANNOTATIONS = (
    str,
    int,
    float,
    bool,
    bytes,
    bytearray,
    type(None),
    dict,
    list,
    tuple,
    set,
    frozenset,
    datetime,
    date,
    datetime_time,
    timedelta,
    Decimal,
    UUID,
    Path,
)

#: The resources bound for a tool call outside a runtime step (`execute_tool(..., resources=...)`).
_bound_resources: ContextVar[RuntimeResources | None] = ContextVar(
    "reactifact_tool_resources", default=None
)


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
            if not output.error
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
    #: Retry a failed attempt (`RetryPolicy`); `None` = fail once.
    retry: RetryPolicy | None = None
    #: Per-attempt deadline in seconds; `None` = no deadline.
    timeout: float | None = None
    _reactifact_instrumented: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        raw = cls.__dict__.get("execute")
        if (
            raw is None
            or getattr(raw, "_reactifact_instrumented", False)
            or getattr(raw, "__isabstractmethod__", False)
        ):
            return

        @functools.wraps(raw)
        async def execute(self: Tool, args: dict[str, Any]) -> ToolOutput:
            return await _execute_instrumented(
                self, lambda call_args: raw(self, call_args), args
            )

        execute._reactifact_instrumented = True  # type: ignore[attr-defined]
        cls.execute = execute  # type: ignore[method-assign]
        cls._raw_execute = raw  # type: ignore[method-assign]
        cls._reactifact_instrumented = True

    async def _raw_execute(self, args: dict[str, Any]) -> ToolOutput:
        """The uninstrumented implementation; set by `__init_subclass__`."""
        raise NotImplementedError

    @abstractmethod
    async def execute(self, args: dict[str, Any]) -> ToolOutput:
        """Execute the operation; failure is an exception or `ToolOutput(error=...)`."""
        ...


def _resolved_annotations(fn: Callable[..., Any]) -> dict[str, Any]:
    """Runtime annotations, resolving `from __future__ import annotations` strings."""
    try:
        return get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001 — a forward ref may still be unresolvable
        return {}


def _annotation_kind(annotation: Any) -> Any:
    """Unwraps `Annotated[...]` so injection/data classification sees the real type."""
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return args[0] if args else Any
    return annotation


def _is_injected(annotation: Any) -> bool:
    """Whether a tool parameter is a collaborator from `RuntimeResources`, not an argument.

    Plain classes (clients, protocols, services) are injected; pydantic models,
    dataclasses, enums and scalars/containers stay in the JSON schema.
    """
    if get_origin(annotation) is Annotated and any(
        isinstance(meta, Inject) for meta in get_args(annotation)[1:]
    ):
        return True
    annotation = _annotation_kind(annotation)
    if annotation is Any or annotation is inspect.Parameter.empty:
        return False
    if not isinstance(annotation, type):
        return False
    if issubclass(annotation, BaseModel) or issubclass(annotation, Enum):
        return False
    if dataclasses.is_dataclass(annotation):
        return False
    return not issubclass(annotation, _DATA_ANNOTATIONS)


def _args_model(
    fn: Callable[..., Any], injected: dict[str, Any] | None = None
) -> type[BaseModel]:
    """Builds a Pydantic model of arguments from the function signature (→ JSON schema).

    Parameters that are injected collaborators are recorded in `injected` and
    omitted from the schema, so the model is never asked for them.
    """
    hints = _resolved_annotations(fn)
    fields: dict[str, Any] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if param.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            continue
        annotation = hints.get(name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = Any
        if _is_injected(annotation) or isinstance(param.default, Inject):
            if injected is not None:
                injected[name] = _annotation_kind(annotation)
            continue
        if param.default is inspect.Parameter.empty:
            fields[name] = (annotation, ...)  # required argument
        else:
            fields[name] = (annotation, param.default)
    return create_model(f"args_{fn.__name__}", **fields)


def _active_resources() -> RuntimeResources | None:
    resources = _bound_resources.get()
    if resources is not None:
        return resources
    from ..step.call import current_step_context

    context: Context | None = current_step_context()
    return context.resources if context is not None else None


class FunctionTool(Tool):
    """Tool from a plain async function: schema is derived from the signature.

    Parameters annotated with a pydantic model/scalar are arguments (the model
    sees them); a plain class annotation is an injected collaborator, resolved
    from `RuntimeResources` like a step parameter:

    ```python
    @tool
    async def get_pod(name: str, k8s: K8sClient) -> Pod:
        return await k8s.get(name)
    ```
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        destructive: bool = False,
        description: str | None = None,
        retry: RetryPolicy | None = None,
        timeout: float | None = None,
    ):
        self._fn = fn
        self._injected: dict[str, Any] = {}
        self.args_model = _args_model(fn, self._injected)
        self.name = name or fn.__name__
        self.destructive = destructive
        self.description = description or (fn.__doc__ or "").strip() or fn.__name__
        self.schema = self.args_model.model_json_schema()
        self.retry = retry
        self.timeout = timeout

    def _service(self, tool_name: str, param: str, annotation: Any) -> Any:
        resources = _active_resources()
        if resources is None:
            raise RuntimeError(
                f"@tool {tool_name!r} injects {annotation.__name__!r} for "
                f"parameter {param!r}, but the tool is not running inside a "
                "step; call it from a step or pass resources= to execute_tool()."
            )
        service = resources.resolve(annotation)
        if service is None:
            raise RuntimeError(
                f"@tool {tool_name!r} needs {annotation.__name__!r} for parameter "
                f"{param!r}; register it on RuntimeResources (or set it on the "
                "Pipeline's resources)."
            )
        return service

    async def execute(self, args: dict[str, Any]) -> ToolOutput:
        validated = self.args_model(**args)
        # Read validated attributes instead of `model_dump()` so a pydantic
        # model argument arrives as a model, not a dict.
        kwargs = {
            name: getattr(validated, name) for name in type(validated).model_fields
        }
        for param, annotation in self._injected.items():
            kwargs[param] = self._service(self.name, param, annotation)
        result = await self._fn(**kwargs)
        if isinstance(result, ToolOutput):
            return result
        if isinstance(result, BaseModel):
            return ToolOutput(data=result.model_dump(mode="json"))
        if (
            isinstance(result, (list, tuple))
            and result
            and all(isinstance(item, BaseModel) for item in result)
        ):
            return ToolOutput(
                data={"items": [item.model_dump(mode="json") for item in result]}
            )
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
    retry: RetryPolicy | None = None,
    timeout: float | None = None,
) -> FunctionTool: ...


@overload
def tool(
    fn: None = None,
    *,
    name: str | None = None,
    destructive: bool = False,
    description: str | None = None,
    retry: RetryPolicy | None = None,
    timeout: float | None = None,
) -> Callable[[Callable[..., Any]], FunctionTool]: ...


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    destructive: bool = False,
    description: str | None = None,
    retry: RetryPolicy | None = None,
    timeout: float | None = None,
) -> Any:
    """Decorator: turns an async function into a `FunctionTool` (schema from the signature).

    `retry=` retries a failed call (`RetryPolicy`), `timeout=` bounds one attempt.
    """

    def wrap(f: Callable[..., Any]) -> FunctionTool:
        return FunctionTool(
            f,
            name=name,
            destructive=destructive,
            description=description,
            retry=retry,
            timeout=timeout,
        )

    return wrap(fn) if fn is not None else wrap
