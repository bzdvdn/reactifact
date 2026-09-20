"""recipes — an opt-in native tool-calling loop (§46, §47).

`reactifact.native_tool_use` deliberately ships *functions* for one native
tool-calling turn, not a loop (see its module docstring) — the framework keeps
that stance. But every RAG/coding app otherwise re-implements the same loop
around those functions: bounded rounds, execute the calls (often in parallel),
feed the results back, force a final answer when the model stops calling tools,
and nudge it when a *mandatory* tool never ran.

This is that loop as an explicit, opt-in **recipe** (not a core primitive, and
not a reactive `Agent`): call it inside a produce when the shape fits, or ignore
it and compose `native_tool_use` yourself. It returns a `ToolLoopResult` with the
final text, the full OpenAI-format transcript, and every tool observation — so a
caller can record which documents/tools were read.

    result = await run_tool_loop(
        call.context,
        system="You are a research assistant.",
        user=question,
        tools=[search_knowledge, read_source],
        mandatory="read_source",
    )
    call.effects.create(Answer(text=result.text))
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..context import Context
from ..native_tool_use import ToolCall, native_complete, parse_tool_calls
from ..tools import Tool


@dataclass
class ToolObservation:
    """One executed tool call: what was called and what came back."""

    call: ToolCall
    output: str = ""
    error: str = ""

    @property
    def name(self) -> str:
        return self.call.name


@dataclass
class ToolLoopResult:
    """The loop's outcome: final text, transcript, and tool observations."""

    text: str
    messages: list[dict[str, Any]]
    observations: list[ToolObservation] = field(default_factory=list)
    rounds: int = 0

    @property
    def tools_used(self) -> list[str]:
        return [obs.name for obs in self.observations]


def _as_message(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.args)},
    }


async def _execute_one(tool: Tool | None, call: ToolCall) -> ToolObservation:
    if tool is None:
        return ToolObservation(call=call, error=f"unknown tool: {call.name!r}")
    output = await tool.execute(call.args)
    if output.error:
        return ToolObservation(call=call, error=output.error)
    if output.text:
        return ToolObservation(call=call, output=output.text)
    return ToolObservation(
        call=call, output=json.dumps(output.data, ensure_ascii=False)
    )


async def _execute(
    tools: dict[str, Tool], calls: list[ToolCall], *, parallel: bool
) -> list[ToolObservation]:
    if parallel and len(calls) > 1:
        return list(
            await asyncio.gather(*(_execute_one(tools.get(c.name), c) for c in calls))
        )
    return [await _execute_one(tools.get(c.name), c) for c in calls]


def _content(observation: ToolObservation) -> str:
    if observation.error:
        return f"error: {observation.error}"
    return observation.output


async def run_tool_loop(
    context: Context,
    *,
    system: str,
    user: str,
    tools: Sequence[Tool] | dict[str, Tool],
    max_rounds: int = 6,
    parallel: bool = True,
    mandatory: str | Sequence[str] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ToolLoopResult:
    """Runs native tool-calling for up to `max_rounds`, then forces an answer.

    - A turn that returns no tool calls ends the loop with its text.
    - Tool calls are executed (in parallel by default) and their results fed
      back as `tool` messages.
    - `mandatory` (a tool name or names that *must* run): while none has been
      called and rounds remain, a reminder is injected so the model is nudged
      rather than the loop silently finishing without it.
    - If `max_rounds` is reached without a final answer, one more call is made
      with no tools, to force a plain-text answer.
    - No provider / a failed call yields `text=""` with whatever was observed
      — the same honest-failure contract the rest of the framework uses.
    """
    tool_map = dict(tools) if isinstance(tools, dict) else {t.name: t for t in tools}
    required = {mandatory} if isinstance(mandatory, str) else set(mandatory or ())
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    observations: list[ToolObservation] = []
    called: set[str] = set()
    rounds = 0

    for _ in range(max(0, max_rounds)):
        response = await native_complete(
            context,
            messages=messages,
            tools=tool_map,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response is None:
            return ToolLoopResult("", messages, observations, rounds)
        rounds += 1
        calls = parse_tool_calls(response)
        if not calls:
            if required - called:
                # mandatory tool not used yet — nudge and keep going
                messages.append({"role": "assistant", "content": response.text})
                missing = ", ".join(sorted(required - called))
                messages.append(
                    {
                        "role": "user",
                        "content": f"You must call {missing} before answering.",
                    }
                )
                continue
            return ToolLoopResult(response.text, messages, observations, rounds)

        messages.append(
            {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [_as_message(c) for c in calls],
            }
        )
        observations.extend(await _execute(tool_map, calls, parallel=parallel))
        called.update(c.name for c in calls)
        for observation in observations[-len(calls) :]:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": observation.call.id,
                    "content": _content(observation),
                }
            )

    # out of rounds — force a plain answer without tools
    response = await native_complete(
        context, messages=messages, temperature=temperature, max_tokens=max_tokens
    )
    text = response.text if response is not None else ""
    return ToolLoopResult(text, messages, observations, rounds)


__all__ = ["ToolLoopResult", "ToolObservation", "run_tool_loop"]
