from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from ...artifacts import Artifact
from ...context import Context
from ...effects import Effects
from ...structured import structured_llm
from ..base import Tool
from .base import (
    ToolAnswer,
    _FinalAnswer,
    _ToolLoopBase,
    _ToolUseStep,
)


class DeferredToolGroup(BaseModel):
    """A named group of tools whose schemas stay out of the prompt until the
    LLM asks for them by name — pass a list to `ToolUse(deferred_tool_groups=)`.

    Meant for many MCP servers/tool sources connected at once: dumping every
    one's full schema into the system prompt on every step burns context for
    tools most steps never touch. Instead, only `tool_names` (bare names, no
    schemas) show up in a compact catalog; `loader` is called at most once
    per group per `ToolUse` run — the first time the LLM asks for it via the
    built-in `load_tools` tool — and its real `Tool`s (with full schemas)
    join the regular tool list from then on, for the rest of that run.

        github_group = DeferredToolGroup(
            group_id="mcp:github",
            display_name="GitHub",
            description="Issues, PRs, repos, code search",
            tool_names=["create_issue", "search_repos"],
            loader=lambda: mcp_stdio_tools("npx", [...]).tools,  # zero-arg async callable
        )
        ToolUse(system, tools=[...], deferred_tool_groups=[github_group])

    Not supported on `ToolUseHITL`: its loop is reactive (one `produce()` per
    LLM step, resumed from `Observation` artifacts between calls, §60), so
    "which groups are already loaded" would need to be persisted state, not
    a local variable — a bigger feature than this one.
    """

    model_config = {"arbitrary_types_allowed": True}

    group_id: str
    display_name: str
    description: str
    tool_names: Sequence[str]
    loader: Callable[[], Any]


class ToolUse(_ToolLoopBase):
    """Blocking loop \"LLM decides → tool → … → answer\" in a single produce.

    Simple, no HITL: the LLM either calls a tool or answers. The logic lives here,
    not in the container agent. Destructive tools are not offered to the LLM.
    """

    def __init__(
        self,
        system: str,
        tools: Sequence[Tool] | dict[str, Tool],
        *,
        name: str = "llm",
        max_steps: int = 8,
        temperature: float | None = None,
        max_tokens: int | None = None,
        deferred_tool_groups: Sequence[DeferredToolGroup] = (),
    ):
        super().__init__(
            system, tools, name=name, temperature=temperature, max_tokens=max_tokens
        )
        self.max_steps = max_steps
        self.deferred_tool_groups = list(deferred_tool_groups)

    async def run(
        self,
        context: Context,
        effects: Effects,
        trigger: Artifact[Any] | None,
    ) -> None:
        if trigger is None or isinstance(trigger.data, ToolAnswer):
            return None  # final answer is handled by user produces
        goal = getattr(trigger.data, "text", "") or ""
        text = await self._loop(context, goal)
        effects.create(ToolAnswer(agent=self.name, query_id=trigger.id, text=text))
        return None

    async def _loop(self, context: Context, goal: str) -> str:
        history: list[str] = []
        # group_id -> {tool name -> Tool}, populated lazily by `load_tools`;
        # local to this call, not `self` — `ToolUse` instances are shared
        # across concurrent produce() runs (§ Step is declared once).
        loaded_groups: dict[str, dict[str, Tool]] = {}
        budget = context.resources.budget
        max_tool_calls = budget.max_tool_calls if budget is not None else None
        # Runtime only enforces Budget.max_seconds *between* agent runs
        # (Runtime._budget_exhausted): this loop makes several LLM/tool
        # round-trips inside one produce(), so without its own check here a
        # slow provider could blow well past the time budget before the
        # runtime ever gets a chance to see it.
        deadline = context.resources.budget_deadline
        tracker = context.resources.budget_tracker
        executed = 0
        context.announce("Deciding next action…", kind="agent", agent=self.name)
        for _ in range(self.max_steps):
            if deadline is not None and time.monotonic() >= deadline:
                break  # time budget exhausted — fall through to the forced answer
            if tracker is not None and tracker.exhausted(budget) is not None:
                break  # token/cost budget exhausted — fall through to the answer
            available = self._available_tools(loaded_groups)
            decision = await structured_llm(
                context,
                schema=_ToolUseStep,
                system=self._system_prompt(available, loaded_groups),
                user=self._user_prompt(goal, history),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            if decision is None:
                return "Could not reach a decision."
            if decision.type == "answer":
                return decision.text
            if not decision.tool:
                return "Tool not specified."
            if decision.tool == "load_tools" and self.deferred_tool_groups:
                history.append(
                    await self._load_tool_group(loaded_groups, decision.args)
                )
                continue
            if max_tool_calls is not None and executed >= max_tool_calls:
                history.append(
                    "Tool budget exhausted; answer based on the available data."
                )
                continue
            context.announce(
                f"Calling tool '{decision.tool}'…", kind="agent", tool=decision.tool
            )
            result = await self._run_tool(
                available, decision.tool, decision.args, context=context
            )
            executed += 1
            history.append(
                f"tool_call: {decision.tool}({json.dumps(decision.args, ensure_ascii=False)})\n"
                f"result: {result}"
            )
        # Loop hit the step limit or the time budget: force the LLM to answer
        # based on the data gathered so far.
        forced = await structured_llm(
            context,
            schema=_FinalAnswer,
            system="Answer now based on the available data. "
            'Reply with strict JSON: {"text":"..."}',
            user=self._user_prompt(goal, history),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if forced is not None and forced.text:
            return forced.text
        return "Step limit reached; answer based on the available data."

    def _available_tools(
        self, loaded_groups: dict[str, dict[str, Tool]]
    ) -> dict[str, Tool]:
        merged = dict(self.tools)
        for group_tools in loaded_groups.values():
            merged.update(group_tools)
        return merged

    async def _load_tool_group(
        self, loaded_groups: dict[str, dict[str, Tool]], args: dict[str, Any]
    ) -> str:
        group_id = args.get("group_id", "")
        group = next(
            (g for g in self.deferred_tool_groups if g.group_id == group_id), None
        )
        if group is None:
            available = ", ".join(g.group_id for g in self.deferred_tool_groups)
            return (
                f"tool_call: load_tools(group_id={group_id!r})\n"
                f"result: Unknown tool group. Available: {available}"
            )
        if group_id not in loaded_groups:
            loaded_groups[group_id] = {t.name: t for t in await group.loader()}
        pool = loaded_groups[group_id]
        requested = args.get("tool_names") or list(pool)
        schemas = "\n".join(
            f"- {pool[n].name}: {pool[n].description}\n"
            f"  args: {json.dumps(pool[n].schema, ensure_ascii=False)}"
            for n in requested
            if n in pool
        )
        return (
            f"tool_call: load_tools(group_id={group_id!r})\n"
            f"result: Loaded. These tools are now available:\n{schemas}"
        )

    def _deferred_groups_block(self, loaded_groups: dict[str, dict[str, Tool]]) -> str:
        remaining = [
            g for g in self.deferred_tool_groups if g.group_id not in loaded_groups
        ]
        if not remaining:
            return ""
        catalog = "\n".join(
            f"- {g.group_id} — {g.display_name}: {g.description} "
            f"({len(g.tool_names)} tools: {', '.join(g.tool_names)})"
            for g in remaining
        )
        return (
            "\nTool groups available on demand (schemas hidden until loaded, "
            "to save context):\n"
            f"{catalog}\n\n"
            "Load a group before using its tools: "
            '{"type":"tool_call","tool":"load_tools","args":{"group_id":"<id>"}}\n'
        )

    def _system_prompt(
        self,
        available: dict[str, Tool] | None = None,
        loaded_groups: dict[str, dict[str, Tool]] | None = None,
    ) -> str:
        tools = available if available is not None else self.tools
        usable = [t for t in tools.values() if not t.destructive]
        schemas = "\n".join(
            f"- {t.name}: {t.description}\n  args: {json.dumps(t.schema, ensure_ascii=False)}"
            for t in usable
        )
        groups_block = (
            self._deferred_groups_block(loaded_groups or {})
            if self.deferred_tool_groups
            else ""
        )
        return (
            f"{self.system}\n\n"
            f"Available tools:\n{schemas}\n"
            f"{groups_block}\n"
            "You work in a loop, one step at a time. Each step reply with strict "
            "JSON matching this schema: "
            '{"type":"tool_call","tool":"<name>","args":{...}} — call a tool, or '
            '{"type":"answer","text":"..."} — final answer.\n'
            "Rules:\n"
            "- Call at most one tool per step, and never call the same tool twice "
            "in a row.\n"
            "- After a tool result, give the final answer on the next step, unless "
            "the result is clearly insufficient.\n"
            "- Call a tool only when you lack the information; finish within at "
            "most 3 tool calls — prefer answering over extra calls."
        )

    @staticmethod
    def _user_prompt(goal: str, history: list[str]) -> str:
        results = "\n\n".join(history)
        return f"Goal: {goal}\n\nTool results so far:\n{results or '—'}"
