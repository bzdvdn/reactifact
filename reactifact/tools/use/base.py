"""Tool-use for LLM agents: \"LLM decides → tool → … → answer\" as a Step.

Two loop variants:
- `ToolUse` — a blocking loop in a single produce (simple, atomic);
- `ToolUseHITL` — reactive, step by step: can ask the human clarifying
  questions (`type:\"ask\"` → `PendingQuestion`) and resume after the answer
  (§60). Intermediate steps are state (`Observation`).

Both end in a `ToolAnswer` artifact (the LLM final answer, §68); your produces
turn it into domain artifacts. `ToolAnswer.agent` distinguishes answers from
different LLM agents in one Runtime.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...context import Context
from ...step import Step
from ..base import Tool, execute_tool


class ToolAnswer(BaseModel):
    """Final LLM answer as an artifact (§68). `agent` — which ToolUse created it."""

    agent: str = ""
    query_id: str = ""
    text: str = ""


class Observation(BaseModel):
    """A step in the reactive loop: a tool result (tool) or a human answer (user)."""

    query_id: str
    text: str
    step: int = 0
    source: str = "tool"
    agent: str = ""


class _ToolUseStep(BaseModel):
    """LLM decision inside the blocking loop: call a tool or answer."""

    type: Literal["tool_call", "answer"]
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    text: str = ""


class _ToolUseStepHITL(BaseModel):
    """LLM decision inside the reactive loop: tool / clarify / answer."""

    type: Literal["tool_call", "answer", "ask"]
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    text: str = ""


class _FinalAnswer(BaseModel):
    """Forced answer when the loop hit the step limit."""

    text: str = ""


class _ToolLoopBase(Step[ToolAnswer]):
    """Shared plumbing for `ToolUse`/`ToolUseHITL`: tool registry + `_run_tool`.

    Both loops offer the same tool set the same way and execute a tool call
    identically (unknown tool / destructive / exception → a text result the
    LLM sees, never an exception out of the produce). Everything that differs
    between the two — the decision schema, the prompt rules, how history is
    represented, whether a clarifying question can be asked — stays on the
    subclass; this base only holds what is genuinely identical.
    """

    artifact_type = ToolAnswer

    def __init__(
        self,
        system: str,
        tools: Sequence[Tool] | dict[str, Tool],
        *,
        name: str = "llm",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        self.name = name
        self.system = system
        self.tools = (
            {t.name: t for t in tools} if not isinstance(tools, dict) else dict(tools)
        )
        self.temperature = temperature
        self.max_tokens = max_tokens
        super().__init__()

    async def _run_tool(
        self,
        tools: dict[str, Tool],
        tool_id: str,
        args: dict[str, Any],
        *,
        context: Context,
        allow_destructive: bool = False,
    ) -> str:
        tool = tools.get(tool_id)
        if tool is None:
            available = ", ".join(tools)
            return f"Unknown tool '{tool_id}'. Available: {available}"
        # Authz: a principal may be denied executing a tool. Denial raises (it is
        # not an agent bug to swallow), propagating like a guardrail violation.
        context.resources.require_authorized("execute", tool_id)
        if tool.destructive and not allow_destructive:
            return f"Tool '{tool_id}' is destructive and not offered to the LLM."
        try:
            output = await execute_tool(tool, args)
        except Exception as exc:  # noqa: BLE001 — tool failure is returned to the LLM
            return f"Tool '{tool_id}' failed: {exc}"
        if output.error:
            return output.error
        return (
            output.text if output.text else json.dumps(output.data, ensure_ascii=False)
        )
