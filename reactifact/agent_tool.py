"""reactifact.agent_tool — a sub-agent as a `Tool` (delegation, §46).

`AgentAsTool` lets an `LLMAgent`/`HITLLMAgent` loop call another agent as an
ordinary tool: "delegate to X" runs a fresh, isolated nested `Runtime` to
completion and returns its final answer as the tool result. The delegating
loop sees only the final text — not the sub-agent's own tool calls, retries,
or internal reasoning.

Two deliberate constraints, not omissions:

- **Isolation is a fresh `Context`, not `context.branch()`.** The sub-agent
  sees only the `query` it's given, nothing of the parent's history — no
  prompt bloat from an unrelated conversation, and no accidental fork/merge
  semantics (`branch()` exists for alternative-state exploration meant to be
  merged back later, §39/§40; a one-shot delegate-and-discard call needs
  neither). If a sub-agent genuinely needs to see the parent's state, build
  its `Context` yourself and don't use this tool.
- **HITL sub-agents are rejected, not silently broken.** `Tool.execute` has
  no channel back to a human — nothing would ever answer a `PendingQuestion`
  the sub-agent raises inside its own isolated `Context`, and the nested run
  would just finish with no `ToolAnswer`. `execute()` checks for this and
  returns a `ToolOutput(error=...)` naming the unanswered question, instead
  of returning empty text with no explanation.

Because `Tool.execute` receives only `args` (no `context`, see `tools.py`),
the sub-agent's `LLMProvider`/budget cannot be inherited implicitly from
whatever loop is calling this tool — pass them explicitly via `resources=`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from .agents import Agent
from .budget import Budget
from .context import Context
from .resources import RuntimeResources
from .runtime import Runtime
from .tool_use import ToolAnswer
from .tools import Tool, ToolOutput


class SubTask(BaseModel):
    """Default input artifact for `AgentAsTool` — override via `input_type`
    with any model that has a `text: str` field (the convention `ToolUse`'s
    own `_goal` reads, `tool_use.py`)."""

    text: str


class AgentAsTool(Tool):
    """Wraps `agent_factory()` as a callable tool (see module docstring)."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        agent_factory: Callable[[], Agent],
        resources: RuntimeResources,
        input_type: type[BaseModel] = SubTask,
        output_type: type[BaseModel] | None = None,
        max_runs: int = 20,
        destructive: bool = False,
    ):
        self.name = name
        self.description = description
        self.agent_factory = agent_factory
        self.resources = resources
        self.input_type = input_type
        self.output_type = output_type or ToolAnswer
        self.max_runs = max_runs
        self.destructive = destructive
        self.schema = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The task/question to delegate to the sub-agent.",
                }
            },
            "required": ["query"],
        }

    async def execute(self, args: dict[str, Any]) -> ToolOutput:
        query = str(args.get("query", ""))
        sub_context = Context(resources=self.resources)
        sub_agent = self.agent_factory()
        sub_runtime = Runtime(
            sub_context, agents=[sub_agent], budget=Budget(max_runs=self.max_runs)
        )
        sub_context.create(self.input_type(text=query))
        await sub_runtime.arun()

        if sub_context.has_pending_question():
            pending = sub_context.latest_pending_question()
            question = pending.data.question if pending is not None else ""
            return ToolOutput(
                error=(
                    f"sub-agent '{sub_agent.name}' needs clarification it wasn't "
                    f"given and can't ask for: {question!r}. Give '{self.name}' a "
                    "more complete query and try again."
                )
            )

        outputs = sub_context.list_artifacts(self.output_type)
        if not outputs:
            return ToolOutput(
                error=f"sub-agent '{sub_agent.name}' produced no {self.output_type.__name__}"
            )
        text = getattr(outputs[-1].data, "text", "") or ""
        return ToolOutput(text=text)
