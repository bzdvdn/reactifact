from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from ...artifacts import Artifact
from ...context import Context
from ...effects import Effects
from ...events import Event
from ...interrupt import PendingQuestion
from ...structured import structured_llm
from ..base import Tool
from .base import (
    Observation,
    ToolAnswer,
    _FinalAnswer,
    _ToolLoopBase,
    _ToolUseStepHITL,
)


class ToolUseHITL(_ToolLoopBase):
    """Reactive loop: step by step, can ask the human (HITL, §60).

    The LLM may answer (`answer`), call a tool (`tool_call`, result goes into an
    `Observation`), or ask a clarifying question (`ask` → `PendingQuestion`).
    The human answer comes back into the loop as `Observation(source="user")`.

    Destructive tools are offered to the LLM (unlike `ToolUse`, which excludes
    them entirely) but never executed straight away: a `tool_call` targeting a
    destructive tool creates a `PendingQuestion(kind="approve")` instead, and
    the call only runs once a human resolves it with an affirmative answer
    (`max_approvals` bounds how many times one conversation can ask).

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
        max_approvals: int = 3,
        resume_announce: Callable[[str], str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        super().__init__(
            system, tools, name=name, temperature=temperature, max_tokens=max_tokens
        )
        self.max_steps = max_steps
        self.max_asks = max_asks
        self.max_approvals = max_approvals
        # App callback: human answer → status message (kind="status").
        self.resume_announce = resume_announce

    async def run(
        self,
        context: Context,
        effects: Effects,
        event: Event | None,
        trigger: Artifact[Any] | None,
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
            effects.create(
                Observation(
                    query_id=qid,
                    step=len(history) + 1,
                    text=extra,
                    source="user",
                    agent=self.name,
                )
            )
            return None

        if kind == "resume_approve":
            question = trigger
            notes = question.data.notes if question is not None else {}
            tool_id = notes.get("tool_id", "")
            args = notes.get("args", {})
            approved = extra.strip().lower() in (
                "yes",
                "y",
                "approve",
                "approved",
                "true",
            )
            if approved:
                result = await self._run_tool(
                    self.tools, tool_id, args, context=context, allow_destructive=True
                )
            else:
                result = f"Tool '{tool_id}' call was not approved by the user."
            effects.create(
                Observation(
                    query_id=qid,
                    step=len(history) + 1,
                    text=result,
                    source="tool",
                    agent=self.name,
                )
            )
            return None

        if len(history) >= self.max_steps:
            await self._forced_answer(context, qid, goal, history, effects)
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
            self._answer(qid, "Could not reach a decision.", effects)
            return None
        if decision.type == "answer":
            self._answer(qid, decision.text, effects)
            return None
        if decision.type == "ask":
            if not decision.text:
                self._answer(qid, "Question not specified.", effects)
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
                effects.create(
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
                effects.create(
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
            effects.ask(
                decision.text,
                kind="clarify",
                notes={"query_id": qid, "agent": self.name},
            )
            return None
        if not decision.tool:
            self._answer(qid, "Tool not specified.", effects)
            return None

        target_tool = self.tools.get(decision.tool)
        if target_tool is not None and target_tool.destructive:
            step = len(history) + 1
            prior = [
                q
                for q in context.list_artifacts(PendingQuestion)
                if q.data.notes.get("query_id") == qid
                and q.data.kind == "approve"
                and q.data.notes.get("tool_id") == decision.tool
                and q.data.notes.get("args") == decision.args
            ]
            if prior:
                pending = [q for q in prior if not q.data.answered]
                if pending:
                    return None  # awaiting the human's decision
                last_approved = (prior[-1].data.resolution or "").strip().lower() in (
                    "yes",
                    "y",
                    "approve",
                    "approved",
                    "true",
                )
                text = (
                    f"You already asked to run '{decision.tool}' with these args and "
                    f"the user {'approved' if last_approved else 'denied'} it. "
                ) + (
                    "Use the result already recorded and proceed."
                    if last_approved
                    else "Do not ask again; proceed without it."
                )
                effects.create(
                    Observation(
                        query_id=qid,
                        step=step,
                        text=text,
                        source="tool",
                        agent=self.name,
                    )
                )
                return None
            approvals_asked = len(
                [
                    q
                    for q in context.list_artifacts(PendingQuestion)
                    if q.data.notes.get("query_id") == qid and q.data.kind == "approve"
                ]
            )
            if approvals_asked >= self.max_approvals:
                effects.create(
                    Observation(
                        query_id=qid,
                        step=step,
                        text=(
                            f"Approval limit ({self.max_approvals}) reached; cannot "
                            f"run destructive tool '{decision.tool}'. Proceed without it."
                        ),
                        source="tool",
                        agent=self.name,
                    )
                )
                return None
            effects.ask(
                f"Approve running destructive tool '{decision.tool}' with args "
                f"{json.dumps(decision.args, ensure_ascii=False)}? (yes/no)",
                kind="approve",
                notes={
                    "query_id": qid,
                    "agent": self.name,
                    "tool_id": decision.tool,
                    "args": decision.args,
                },
            )
            return None

        tool_history = [o for o in history if o.source == "tool"]
        budget = context.resources.budget
        max_tool_calls = budget.max_tool_calls if budget is not None else None
        tracker = context.resources.budget_tracker
        if tracker is not None and tracker.exhausted(budget) is not None:
            effects.create(
                Observation(
                    query_id=qid,
                    step=len(history) + 1,
                    text=(
                        "Token/cost budget exhausted; "
                        "answer based on the available data."
                    ),
                    source="tool",
                    agent=self.name,
                )
            )
            return None
        if max_tool_calls is not None and len(tool_history) >= max_tool_calls:
            effects.create(
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
        result = await self._run_tool(
            self.tools, decision.tool, decision.args, context=context
        )
        effects.create(
            Observation(
                query_id=qid,
                step=len(history) + 1,
                text=result,
                source="tool",
                agent=self.name,
            )
        )
        return None

    def _answer(self, qid: str, text: str, effects: Effects) -> None:
        effects.create(ToolAnswer(agent=self.name, query_id=qid, text=text))

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
            resume_kind = "resume_approve" if data.kind == "approve" else "resume"
            return qid, resume_kind, data.resolution or ""
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
        self,
        context: Context,
        qid: str,
        goal: str,
        history: list[Observation],
        effects: Effects,
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
        self._answer(qid, text, effects)

    def _system_prompt(self) -> str:
        schemas = "\n".join(
            f"- {t.name}: {t.description}"
            + (
                " (destructive — running it requires human approval; you may "
                "still call it, the user will be asked to confirm first)"
                if t.destructive
                else ""
            )
            + f"\n  args: {json.dumps(t.schema, ensure_ascii=False)}"
            for t in self.tools.values()
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
