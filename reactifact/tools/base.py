"""Tools: the \"function + schema\" contract for LLM agents (§46–47, §68).

`@tool` turns an async function into a `FunctionTool` (name, description and JSON
argument schema are derived from the signature). Tool usage — see `llm_agent.LLMAgent`:
the \"which tool and with which arguments\" decision loop is driven by the LLM, the
framework executes.

Tools are capabilities; they are not part of consume/produce: those bind artifacts.
"""

from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, overload

from pydantic import BaseModel

from ..policy import RetryPolicy
from .contracts import ToolOutput
from .runlog import _bound_resources, _execute_instrumented
from .schema import _args_model

if TYPE_CHECKING:
    from ..context import Context
    from ..resources import RuntimeResources


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
    #: Optional progress template (`"Looking at {service}"`); the tool loop
    #: formats it with the call arguments after redaction.
    progress: str | None = None
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
        progress: str | None = None,
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
        self.progress = progress

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
    progress: str | None = None,
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
    progress: str | None = None,
) -> Callable[[Callable[..., Any]], FunctionTool]: ...


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    destructive: bool = False,
    description: str | None = None,
    retry: RetryPolicy | None = None,
    timeout: float | None = None,
    progress: str | None = None,
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
            progress=progress,
        )

    return wrap(fn) if fn is not None else wrap


def tool_progress(
    tool: Tool,
    args: dict[str, Any],
    redactor: Any | None = None,
) -> str | None:
    """Formats `tool.progress` with the call arguments, then redacts it."""
    template = getattr(tool, "progress", None)
    if not template:
        return None
    try:
        text = template.format(**args)
    except (KeyError, IndexError, ValueError):
        return None
    if redactor is not None:
        text = redactor.redact(text)
    return str(text)


__all__ = ["FunctionTool", "Tool", "tool", "tool_progress"]
