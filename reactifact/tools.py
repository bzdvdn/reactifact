"""Tools: the \"function + schema\" contract for LLM agents (§46–47, §68).

`@tool` turns an async function into a `FunctionTool` (name, description and JSON
argument schema are derived from the signature). Tool usage — see `llm_agent.LLMAgent`:
the \"which tool and with which arguments\" decision loop is driven by the LLM, the
framework executes.

Tools are capabilities; they are not part of consume/produce: those bind artifacts.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, overload

from pydantic import BaseModel, Field, create_model


class ToolOutput(BaseModel):
    """Tool execution result. `error` is set on failure (not an exception)."""

    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


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
