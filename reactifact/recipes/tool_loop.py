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
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ..context import Context
from ..native_tool_use import (
    ToolCall,
    looks_like_tool_markup,
    native_complete,
    parse_tool_calls,
)
from ..providers import LLMResponse
from ..tools import Tool, ToolError, execute_tool


@dataclass
class ToolObservation:
    """One executed tool call: what was called and what came back."""

    call: ToolCall
    output: str = ""
    error: str = ""
    #: Structured failure details when the tool provided them.
    error_info: ToolError | None = None
    #: Executions used for this call (1 unless a retryable error was retried).
    attempts: int = 1

    @property
    def name(self) -> str:
        return self.call.name


#: Why `run_tool_loop` stopped.
ToolLoopStopReason = Literal[
    "completed", "max_rounds", "budget", "provider_error", "fallback"
]


@dataclass
class ToolLoopResult:
    """The loop's outcome: final text, transcript, and tool observations."""

    text: str
    messages: list[dict[str, Any]]
    observations: list[ToolObservation] = field(default_factory=list)
    rounds: int = 0
    #: `completed` (the model answered), `max_rounds`, `budget` (tool/time/
    #: token/cost), `provider_error` (no provider or a failed call), or
    #: `fallback` (the textual-markup hook produced the answer).
    stop_reason: ToolLoopStopReason = "completed"
    #: Human-readable failure detail, set for `provider_error`.
    error: str | None = None

    @property
    def tools_used(self) -> list[str]:
        return [obs.name for obs in self.observations]


def _as_message(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.args)},
    }


def _observation(call: ToolCall, output: Any, *, attempts: int) -> ToolObservation:
    if output.error_info is not None:
        return ToolObservation(
            call=call,
            error=output.error or str(output.error_info),
            error_info=output.error_info,
            attempts=attempts,
        )
    if output.error:
        return ToolObservation(call=call, error=output.error, attempts=attempts)
    if output.text:
        return ToolObservation(call=call, output=output.text, attempts=attempts)
    return ToolObservation(
        call=call,
        output=json.dumps(output.data, ensure_ascii=False),
        attempts=attempts,
    )


async def _execute_one(
    tool: Tool | None,
    call: ToolCall,
    *,
    retry_attempts: int = 1,
    retry_delay: float = 0.0,
) -> ToolObservation:
    if tool is None:
        return ToolObservation(call=call, error=f"unknown tool: {call.name!r}")
    for attempt in range(1, retry_attempts + 1):
        output = await execute_tool(tool, call.args)
        retryable = output.error_info is not None and output.error_info.retryable
        if retryable and attempt < retry_attempts:
            await asyncio.sleep(retry_delay * (2 ** (attempt - 1)))
            continue
        return _observation(call, output, attempts=attempt)
    raise AssertionError("unreachable: the loop always returns")


async def _execute(
    tools: dict[str, Tool],
    calls: list[ToolCall],
    *,
    parallel: bool,
    retry_attempts: int = 1,
    retry_delay: float = 0.0,
) -> list[ToolObservation]:
    if parallel and len(calls) > 1:
        return list(
            await asyncio.gather(
                *(
                    _execute_one(
                        tools.get(c.name),
                        c,
                        retry_attempts=retry_attempts,
                        retry_delay=retry_delay,
                    )
                    for c in calls
                )
            )
        )
    return [
        await _execute_one(
            tools.get(c.name),
            c,
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
        )
        for c in calls
    ]


def _content(observation: ToolObservation) -> str:
    if observation.error:
        return f"error: {observation.error}"
    return observation.output


def _truncate(text: str, limit: int | None) -> str:
    """Caps an observation fed to the LLM, with an explicit truncation marker."""
    if limit is None or len(text) <= limit:
        return text
    return f"{text[:limit]}\n…[truncated {len(text) - limit} chars]"


#: Event name + payload; the same payload also goes to `Context.announce`.
OnToolLoopEvent = Callable[[str, dict[str, Any]], Any]


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
    max_observation_chars: int | None = None,
    redact_tool_outputs: bool = False,
    retry_tool_errors: bool = False,
    tool_retry_attempts: int = 2,
    tool_retry_delay: float = 0.5,
    parse_calls: Callable[[LLMResponse], list[ToolCall]] | None = None,
    fallback: Callable[[Context, list[dict[str, Any]], str], Awaitable[str | None]]
    | None = None,
    on_event: OnToolLoopEvent | None = None,
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
    - The turn's `Budget` (`context.resources.budget`) is honoured between
      rounds, like `ToolUse`'s own loop: `max_tool_calls` caps executed calls,
      `max_seconds` (via `budget_deadline`) and token/cost limits stop new
      rounds. The exhausted calls are reported back as tool errors, then the
      loop falls through to the forced answer.
    - `max_observation_chars` truncates each tool result before it goes back
      to the model (the returned `observations` keep the full text).
    - `redact_tool_outputs=True` applies `context.resources.redactor` to tool
      outputs/errors before they reach the model.
    - `retry_tool_errors=True` retries a tool call when it returns a
      `ToolError(retryable=True)`, up to `tool_retry_attempts` total attempts
      with exponential `tool_retry_delay` backoff. Retries count as tool
      executions for `Budget.max_tool_calls`.
    - `parse_calls` overrides how a response's tool calls are extracted (e.g.
      a textual DSML/XML codec); `fallback(ctx, messages, text)` runs when no
      calls were parsed and the text looks like tool-call markup, and its
      string (when not `None`) becomes the final answer.
    - `on_event(name, payload)` and `Context.announce(kind="agent")` report
      progress for streaming: `round_start`, `tool_call`, `tool_result`,
      `final`.
    - No provider / a failed call yields `text=""` with whatever was observed
      — the same honest-failure contract the rest of the framework uses.
    """
    tool_map = dict(tools) if isinstance(tools, dict) else {t.name: t for t in tools}
    parse = parse_calls or parse_tool_calls
    redactor = context.resources.redactor if redact_tool_outputs else None
    if tool_retry_attempts < 1:
        raise ValueError("tool_retry_attempts must be >= 1")
    retry_attempts = tool_retry_attempts if retry_tool_errors else 1
    retry_delay = tool_retry_delay if retry_tool_errors else 0.0
    required = {mandatory} if isinstance(mandatory, str) else set(mandatory or ())
    budget = context.resources.budget
    max_tool_calls = budget.max_tool_calls if budget is not None else None
    deadline = context.resources.budget_deadline
    tracker = context.resources.budget_tracker
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    observations: list[ToolObservation] = []
    called: set[str] = set()
    rounds = 0
    executed = 0

    def budget_spent() -> bool:
        return (deadline is not None and time.monotonic() >= deadline) or (
            tracker is not None
            and budget is not None
            and tracker.exhausted(budget) is not None
        )

    async def emit(name: str, payload: dict[str, Any]) -> None:
        message = str(payload.get("message", name))
        data = {key: value for key, value in payload.items() if key != "message"}
        context.announce(message, kind="agent", **data)
        if on_event is not None:
            result = on_event(name, payload)
            if inspect.isawaitable(result):
                await result

    def sanitize(observation: ToolObservation) -> ToolObservation:
        if redactor is None:
            return observation
        return ToolObservation(
            call=observation.call,
            output=redactor.redact(observation.output),
            error=redactor.redact(observation.error),
            error_info=observation.error_info,
            attempts=observation.attempts,
        )

    stop_reason: ToolLoopStopReason = "completed"
    for _ in range(max(0, max_rounds)):
        if budget_spent():
            stop_reason = "budget"
            break  # time/token/cost budget spent — fall through to the answer
        await emit("round_start", {"round": rounds + 1})
        response = await native_complete(
            context,
            messages=messages,
            tools=tool_map,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response is None:
            return ToolLoopResult(
                "",
                messages,
                observations,
                rounds,
                stop_reason="provider_error",
                error="provider call failed or no provider is configured",
            )
        rounds += 1
        calls = parse(response)
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
            if fallback is not None and looks_like_tool_markup(response.text):
                fallback_text = await fallback(context, messages, response.text)
                if fallback_text is not None:
                    await emit("final", {"text": fallback_text, "fallback": True})
                    return ToolLoopResult(
                        fallback_text,
                        messages,
                        observations,
                        rounds,
                        stop_reason="fallback",
                    )
            await emit("final", {"text": response.text})
            return ToolLoopResult(response.text, messages, observations, rounds)

        messages.append(
            {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [_as_message(c) for c in calls],
            }
        )
        to_run = calls
        skipped: list[ToolCall] = []
        if max_tool_calls is not None:
            remaining = max(0, max_tool_calls - executed)
            to_run, skipped = calls[:remaining], calls[remaining:]
        for call in to_run:
            await emit(
                "tool_call",
                {
                    "message": f"Calling tool '{call.name}'…",
                    "tool": call.name,
                    "args": call.args,
                },
            )
        batch = (
            await _execute(
                tool_map,
                to_run,
                parallel=parallel,
                retry_attempts=retry_attempts,
                retry_delay=retry_delay,
            )
            if to_run
            else []
        )
        batch.extend(
            ToolObservation(
                call=call,
                error="tool-call budget exhausted; answer from the data already gathered",
            )
            for call in skipped
        )
        batch = [sanitize(observation) for observation in batch]
        executed += sum(observation.attempts for observation in batch[: len(to_run)])
        observations.extend(batch)
        called.update(c.name for c in to_run)
        for observation in batch:
            content = _content(observation)
            await emit(
                "tool_result",
                {
                    "message": (
                        f"Tool '{observation.name}' failed"
                        if observation.error
                        else f"Tool '{observation.name}' returned {len(content)} chars"
                    ),
                    "tool": observation.name,
                    "error": bool(observation.error),
                    "chars": len(content),
                },
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": observation.call.id,
                    "content": _truncate(content, max_observation_chars),
                }
            )
        if skipped:
            stop_reason = "budget"
            break  # tool-call budget spent — force the answer now

    # out of rounds (or budget) — force a plain answer without tools
    if stop_reason == "completed":
        stop_reason = "max_rounds"
    response = await native_complete(
        context, messages=messages, temperature=temperature, max_tokens=max_tokens
    )
    text = response.text if response is not None else ""
    if response is None and stop_reason == "max_rounds":
        stop_reason = "provider_error"
    await emit("final", {"text": text})
    return ToolLoopResult(
        text,
        messages,
        observations,
        rounds,
        stop_reason=stop_reason,
        error=(
            "provider call failed or no provider is configured"
            if response is None
            else None
        ),
    )


__all__ = [
    "ToolLoopResult",
    "ToolLoopStopReason",
    "ToolObservation",
    "run_tool_loop",
]
