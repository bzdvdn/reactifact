"""The `quick.tools_agent` entry point: an LLM with tools (blocking or HITL)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from ..agents import Agent
from ..budget import Budget
from ..consume import Consume
from ..context import Context
from ..llm_agent import HITLLMAgent, LLMAgent
from ..providers import LLMProvider
from ..tool_use import ToolAnswer
from ..tools import Tool
from ._shared import _arun, _default_resources
from .models import Question


class _QuickLLMAgent(LLMAgent):
    def __init__(
        self,
        *,
        name: str,
        system: str,
        tools: Sequence[Tool],
        question_type: type[BaseModel],
        max_steps: int,
        temperature: float | None,
        max_tokens: int | None,
    ):
        self.system = system
        self.tools = list(tools)
        self.max_steps = max_steps
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.consumes = [Consume(question_type)]
        self.produces = []
        super().__init__(name=name)


class _QuickHITLLMAgent(HITLLMAgent):
    def __init__(
        self,
        *,
        name: str,
        system: str,
        tools: Sequence[Tool],
        question_type: type[BaseModel],
        max_steps: int,
        max_asks: int,
        temperature: float | None,
        max_tokens: int | None,
    ):
        self.system = system
        self.tools = list(tools)
        self.max_steps = max_steps
        self.max_asks = max_asks
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.consumes = [Consume(question_type)]
        self.produces = []
        super().__init__(name=name)


class QuickToolsAgent:
    """An LLM + tools agent, built by `tools_agent(...)`.

    `.ask()` runs one turn and returns the final text (or `None` when no
    provider/reply); `.agent` is the underlying `LLMAgent`/`HITLLMAgent`, so it
    mounts on your own `Runtime` unchanged.
    """

    def __init__(
        self,
        *,
        system: str,
        tools: Sequence[Tool],
        name: str = "assistant",
        human: bool = False,
        question_type: type[BaseModel] = Question,
        max_steps: int = 8,
        max_asks: int = 2,
        temperature: float | None = None,
        max_tokens: int | None = None,
        llm: LLMProvider | None = None,
        budget: Budget | None = None,
        tracer: Any = None,
    ):
        self.question_type = question_type
        if human:
            self.agent: Agent = _QuickHITLLMAgent(
                name=name,
                system=system,
                tools=tools,
                question_type=question_type,
                max_steps=max_steps,
                max_asks=max_asks,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        else:
            self.agent = _QuickLLMAgent(
                name=name,
                system=system,
                tools=tools,
                question_type=question_type,
                max_steps=max_steps,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        self.resources = _default_resources(llm=llm)
        self.budget = budget
        self.tracer = tracer
        self.context: Context | None = None

    async def ask(self, text: str, *, context: Context | None = None) -> str | None:
        ctx = context if context is not None else Context(resources=self.resources)
        ctx.create(self.question_type(text=text))
        await _arun(ctx, [self.agent], budget=self.budget, tracer=self.tracer)
        self.context = ctx
        artifact = ctx.latest(ToolAnswer)
        return artifact.data.text if artifact is not None else None


def tools_agent(
    system: str,
    tools: Sequence[Tool],
    *,
    name: str = "assistant",
    human: bool = False,
    question_type: type[BaseModel] = Question,
    max_steps: int = 8,
    max_asks: int = 2,
    temperature: float | None = None,
    max_tokens: int | None = None,
    llm: LLMProvider | None = None,
    budget: Budget | None = None,
    tracer: Any = None,
) -> QuickToolsAgent:
    """An LLM agent with tools: `human=False` → blocking `LLMAgent`,
    `human=True` → the reactive `HITLLMAgent` that can ask clarifying questions.

    `.ask(text)` is the convenience path; `.agent` is the real agent for use in
    your own `Runtime`/`ChatAssistant`. Pass `question_type=` for a domain input
    model (must have a `text: str` field).
    """
    return QuickToolsAgent(
        system=system,
        tools=tools,
        name=name,
        human=human,
        question_type=question_type,
        max_steps=max_steps,
        max_asks=max_asks,
        temperature=temperature,
        max_tokens=max_tokens,
        llm=llm,
        budget=budget,
        tracer=tracer,
    )


__all__ = ["QuickToolsAgent", "tools_agent"]
