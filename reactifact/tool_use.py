"""Tool-use for LLM agents: \"LLM decides → tool → … → answer\" as a Produce.

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
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from .artifacts import Artifact
from .context import Context
from .events import Event
from .interrupt import PendingQuestion
from .produce import Produce
from .structured import structured_llm
from .tools import Tool


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


class _ToolLoopBase(Produce[ToolAnswer]):
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
        self, tools: dict[str, Tool], tool_id: str, args: dict[str, Any]
    ) -> str:
        tool = tools.get(tool_id)
        if tool is None:
            available = ", ".join(tools)
            return f"Unknown tool '{tool_id}'. Available: {available}"
        if tool.destructive:
            return f"Tool '{tool_id}' is destructive and not offered to the LLM."
        try:
            output = await tool.execute(args)
        except Exception as exc:  # noqa: BLE001 — tool failure is returned to the LLM
            return f"Tool '{tool_id}' failed: {exc}"
        if output.error:
            return output.error
        return (
            output.text if output.text else json.dumps(output.data, ensure_ascii=False)
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

    async def produce(
        self,
        context: Context,
        inputs: list[Artifact[Any]],
        event: Event | None = None,
    ) -> None:
        artifact = context.get(event.artifact_id) if event is not None else None
        if artifact is None or isinstance(artifact.data, ToolAnswer):
            return None  # final answer is handled by user produces
        goal = getattr(artifact.data, "text", "") or ""
        text = await self._loop(context, goal)
        self.effects.create(
            ToolAnswer(agent=self.name, query_id=artifact.id, text=text)
        )
        return None

    async def _loop(self, context: Context, goal: str) -> str:
        history: list[str] = []
        # group_id -> {tool name -> Tool}, populated lazily by `load_tools`;
        # local to this call, not `self` — `ToolUse` instances are shared
        # across concurrent produce() runs (§ Produce is declared once).
        loaded_groups: dict[str, dict[str, Tool]] = {}
        budget = context.resources.budget
        max_tool_calls = budget.max_tool_calls if budget is not None else None
        # Runtime only enforces Budget.max_seconds *between* agent runs
        # (Runtime._budget_exhausted): this loop makes several LLM/tool
        # round-trips inside one produce(), so without its own check here a
        # slow provider could blow well past the time budget before the
        # runtime ever gets a chance to see it.
        deadline = context.resources.budget_deadline
        executed = 0
        context.announce("Deciding next action…", kind="agent", agent=self.name)
        for _ in range(self.max_steps):
            if deadline is not None and time.monotonic() >= deadline:
                break  # time budget exhausted — fall through to the forced answer
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
            result = await self._run_tool(available, decision.tool, decision.args)
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


class ToolUseHITL(_ToolLoopBase):
    """Reactive loop: step by step, can ask the human (HITL, §60).

    The LLM may answer (`answer`), call a tool (`tool_call`, result goes into an
    `Observation`), or ask a clarifying question (`ask` → `PendingQuestion`).
    The human answer comes back into the loop as `Observation(source="user")`.

    `_history`/the `ask` dedup check filter `context.list_artifacts(Observation
    | PendingQuestion)` by `query_id` in Python — O(count of that type in the
    whole context), not indexed by `query_id`. Bounded per conversation
    (`max_steps`, `max_asks`), so this is only a real cost if *many*
    long-running conversations share one `Context` — the standard
    one-`Context`-per-session pattern (`SessionStore`, every example in this
    repo) keeps each conversation's own artifact count small regardless of
    how many sessions exist. A real fix would need `RelationGraph` indexed by
    `source_id` (it currently isn't either) plus linking each Observation to
    its goal artifact instead of filtering by field — deliberately not done
    here; flag it if you're sharing one long-lived `Context` across many
    concurrent tool-use conversations.
    """

    def __init__(
        self,
        system: str,
        tools: Sequence[Tool] | dict[str, Tool],
        *,
        name: str = "llm",
        max_steps: int = 8,
        max_asks: int = 2,
        resume_announce: Callable[[str], str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        super().__init__(
            system, tools, name=name, temperature=temperature, max_tokens=max_tokens
        )
        self.max_steps = max_steps
        self.max_asks = max_asks
        # App callback: human answer → status message (kind="status").
        self.resume_announce = resume_announce

    async def produce(
        self,
        context: Context,
        inputs: list[Artifact[Any]],
        event: Event | None = None,
    ) -> None:
        resolved = self._resolve(context, event)
        if resolved is None:
            return None
        qid, kind, extra = resolved
        goal = self._goal(context, qid)
        history = self._history(context, qid)

        if kind == "resume":
            # human answer to a clarify → user observation, continue.
            # Status from the app (kind="status"): text authored by the app.
            if self.resume_announce is not None:
                context.announce(
                    self.resume_announce(extra), kind="status", agent=self.name
                )
            self.effects.create(
                Observation(
                    query_id=qid,
                    step=len(history) + 1,
                    text=extra,
                    source="user",
                    agent=self.name,
                )
            )
            return None

        if len(history) >= self.max_steps:
            await self._forced_answer(context, qid, goal, history)
            return None

        decision = await structured_llm(
            context,
            schema=_ToolUseStepHITL,
            system=self._system_prompt(),
            user=self._user_prompt(goal, history),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if decision is None:
            self._answer(qid, "Could not reach a decision.")
            return None
        if decision.type == "answer":
            self._answer(qid, decision.text)
            return None
        if decision.type == "ask":
            if not decision.text:
                self._answer(qid, "Question not specified.")
                return None
            asked = [
                q
                for q in context.list_artifacts(PendingQuestion)
                if q.data.notes.get("query_id") == qid
                and q.data.question == decision.text
            ]
            if asked:
                answers = [q.data.resolution for q in asked if q.data.answered]
                if not answers:
                    # the same question was already asked and awaits an answer
                    return None
                # LLM asks the same thing again — nudge it to continue
                self.effects.create(
                    Observation(
                        query_id=qid,
                        step=len(history) + 1,
                        text=(
                            f"You already asked '{decision.text}' and the user "
                            f"answered: {answers[-1]}. Use that answer and "
                            "proceed to a final answer."
                        ),
                        source="tool",
                        agent=self.name,
                    )
                )
                return None
            asked_count = len(
                [
                    q
                    for q in context.list_artifacts(PendingQuestion)
                    if q.data.notes.get("query_id") == qid
                ]
            )
            if asked_count >= self.max_asks:
                # no more questions — continue with what we have
                self.effects.create(
                    Observation(
                        query_id=qid,
                        step=len(history) + 1,
                        text=(
                            "You have already asked enough clarifying questions. "
                            "Proceed with the tool call using the available values; "
                            "if a value is missing, use a reasonable default and "
                            "note it in the final answer."
                        ),
                        source="tool",
                        agent=self.name,
                    )
                )
                return None
            self.effects.ask(
                decision.text,
                kind="clarify",
                notes={"query_id": qid, "agent": self.name},
            )
            return None
        if not decision.tool:
            self._answer(qid, "Tool not specified.")
            return None

        tool_history = [o for o in history if o.source == "tool"]
        budget = context.resources.budget
        max_tool_calls = budget.max_tool_calls if budget is not None else None
        if max_tool_calls is not None and len(tool_history) >= max_tool_calls:
            self.effects.create(
                Observation(
                    query_id=qid,
                    step=len(history) + 1,
                    text=(
                        f"Tool budget ({max_tool_calls}) exhausted; "
                        "answer based on the available data."
                    ),
                    source="tool",
                    agent=self.name,
                )
            )
            return None
        context.announce(
            f"Calling tool '{decision.tool}'…", kind="agent", tool=decision.tool
        )
        result = await self._run_tool(self.tools, decision.tool, decision.args)
        self.effects.create(
            Observation(
                query_id=qid,
                step=len(history) + 1,
                text=result,
                source="tool",
                agent=self.name,
            )
        )
        return None

    def _answer(self, qid: str, text: str) -> None:
        self.effects.create(ToolAnswer(agent=self.name, query_id=qid, text=text))

    def _resolve(
        self, context: Context, event: Event | None
    ) -> tuple[str, str, str] | None:
        """(query_id, kind, extra): start / continue / resume (human answer)."""
        artifact = context.get(event.artifact_id) if event is not None else None
        if artifact is None:
            return None
        data = artifact.data
        if isinstance(data, Observation):
            return data.query_id, "continue", ""
        if isinstance(data, PendingQuestion):
            if not data.answered:
                return None  # waiting for the human answer
            qid = data.notes.get("query_id")
            if not qid:
                return None
            return qid, "resume", data.resolution or ""
        if isinstance(data, ToolAnswer):
            return None  # final answer is handled by user produces
        return artifact.id, "start", ""

    @staticmethod
    def _goal(context: Context, qid: str) -> str:
        artifact = context.get(qid)
        if artifact is None:
            return ""
        return getattr(artifact.data, "text", "") or ""

    @staticmethod
    def _history(context: Context, qid: str) -> list[Observation]:
        observations = [
            o for o in context.list_artifacts(Observation) if o.data.query_id == qid
        ]
        observations.sort(key=lambda o: o.data.step)
        return [o.data for o in observations]

    async def _forced_answer(
        self, context: Context, qid: str, goal: str, history: list[Observation]
    ) -> None:
        forced = await structured_llm(
            context,
            schema=_FinalAnswer,
            system="Answer now based on the available data. "
            'Reply with strict JSON: {"text":"..."}',
            user=self._user_prompt(goal, history),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        text = (
            forced.text
            if forced is not None and forced.text
            else "Step limit reached; answer based on the available data."
        )
        self._answer(qid, text)

    def _system_prompt(self) -> str:
        usable = [t for t in self.tools.values() if not t.destructive]
        schemas = "\n".join(
            f"- {t.name}: {t.description}\n  args: {json.dumps(t.schema, ensure_ascii=False)}"
            for t in usable
        )
        return (
            f"{self.system}\n\n"
            f"Available tools:\n{schemas}\n\n"
            "You work in a loop, one step at a time. Each step reply with strict "
            "JSON matching this schema: "
            '{"type":"tool_call","tool":"<name>","args":{...}} — call a tool, '
            '{"type":"ask","text":"..."} — ask the user a clarifying question, or '
            '{"type":"answer","text":"..."} — final answer.\n'
            "Rules:\n"
            "- If you lack context a tool needs (e.g. namespace, repository, role), "
            "ask the user via type:ask — never guess or invent it.\n"
            "- Ask at most one question per missing value, and NEVER ask for the "
            "same value twice. Once the user answers, use that answer as-is and "
            "continue with the tool call — proceed even if the answer is short or "
            "unusual.\n"
            "- Call at most one tool per step, and never call the same tool twice "
            "in a row.\n"
            "- After a tool result, give the final answer on the next step, unless "
            "the result is clearly insufficient.\n"
            "- Call a tool only when you lack the information; finish within at "
            "most 3 tool calls — prefer answering over extra calls."
        )

    @staticmethod
    def _user_prompt(goal: str, history: list[Observation]) -> str:
        parts = [
            f"user answer: {o.text}" if o.source == "user" else f"tool result: {o.text}"
            for o in history
        ]
        return f"Goal: {goal}\n\nContext so far:\n" + ("\n".join(parts) or "—")
