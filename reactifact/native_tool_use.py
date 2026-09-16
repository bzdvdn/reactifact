"""reactifact.native_tool_use — OpenAI-style native tool-calling
(`message.tool_calls`), as composable functions, not a parallel loop class.

`reactifact.providers.LLMProvider`/`LLMRequest` are text-only by design: no
`tools`/`tool_calls` fields. That's not a missing feature to patch with a
new execution loop — `LLMRequest.extra` already forwards arbitrary
provider-specific fields verbatim, and `LLMResponse.raw` already preserves
the full, unparsed response body (`providers/chat.py`'s `OpenAICompatProvider`
confirms both). What's genuinely missing is small, focused helpers for this
one well-known wire shape: building the `tools=[...]` array from `Tool`s,
and parsing `message.tool_calls` back out — not a `ToolUseHITL`-shaped
reactive loop with its own persisted-history artifact and approval gate.

That loop *was* built here initially, and got cut back to this: nothing
concrete has asked for a reactive, multi-round, HITL-gated native
tool-calling loop yet — real usage turned out to be a single decision step
inside a caller's own produce, with its own retry/fallback logic and its
own message-history shape already built from domain artifacts. Building
the bigger thing on spec, before a second real use case asks for it,
would have been exactly the kind of complexity these functions exist to
avoid. If a genuine multi-round native tool-calling conversation with an
approval gate on destructive tools shows up, build that loop against a
concrete case then — these functions are what it would be built on top of.

    tools = tools_payload([search_knowledge, read_source])
    response = await native_complete(context, messages=my_messages, tools=tools)
    calls = parse_tool_calls(response) if response is not None else []
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, cast

from pydantic import BaseModel, Field

from .context import Context
from .providers import LLMRequest, LLMResponse, Message, Role
from .tools import Tool

logger = logging.getLogger(__name__)


class ToolCall(BaseModel):
    """One parsed entry from `message.tool_calls` (OpenAI wire format)."""

    id: str = ""
    name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)


def tools_payload(tools: Sequence[Tool] | dict[str, Tool]) -> list[dict[str, Any]]:
    """Builds the OpenAI `tools=[...]` array from `Tool`s — `Tool.schema` is
    already the right JSON-schema shape (`tools.py`), this just wraps it."""
    values = tools.values() if isinstance(tools, dict) else tools
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.schema,
            },
        }
        for t in values
    ]


def parse_tool_calls(response: LLMResponse) -> list[ToolCall]:
    """Extracts `message.tool_calls` from the provider's raw response.

    `LLMResponse.raw` is the full, unparsed JSON body every
    `OpenAICompatProvider`-family provider already returns
    (`providers/chat.py`) — this is deliberately the only place that reaches
    into it, so a provider shaping `raw` differently only needs to override
    this one function, not anything that calls it. Malformed `arguments`
    JSON parses to `{}` rather than raising (§67 — the model's mistake is
    not a crash), same tolerance `structured_llm.parse_structured` uses.
    """
    if not isinstance(response.raw, dict):
        return []
    choices = response.raw.get("choices") or []
    if not choices:
        return []
    message = choices[0].get("message") or {}
    calls = message.get("tool_calls") or []
    parsed: list[ToolCall] = []
    for call in calls:
        function = call.get("function") or {}
        raw_args = function.get("arguments") or "{}"
        try:
            args = json.loads(raw_args)
        except ValueError:
            logger.debug(
                "native_tool_use: malformed tool_call arguments: %.160r", raw_args
            )
            args = {}
        parsed.append(
            ToolCall(id=call.get("id", ""), name=function.get("name", ""), args=args)
        )
    return parsed


async def native_complete(
    context: Context,
    *,
    messages: list[dict[str, Any]],
    tools: Sequence[Tool] | dict[str, Tool] = (),
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> LLMResponse | None:
    """One native tool-calling turn.

    `messages` are raw OpenAI-format dicts (system/user/assistant-with-
    `tool_calls`/tool-with-`tool_call_id`) — not `providers.Message`, which
    has no field for either. The full, verbatim dicts go via
    `LLMRequest.extra["messages"]`, which every `OpenAICompatProvider`-family
    provider applies *after* building its own messages list from
    `LLMRequest.messages` (`providers/chat.py`'s `_payload` — `request.extra`
    is merged into the payload last, overriding anything built from typed
    fields). That's the documented escape hatch for provider-specific wire
    shapes, not a hack — see `_payload`'s own comment on arbitrary `extra`
    fields.

    `LLMRequest.messages` is *also* populated, with a best-effort typed
    rendering (role + content, `tool_calls`/`tool_call_id` dropped — `Message`
    has no field for either). `OpenAICompatProvider` ignores it in favor of
    the full-fidelity `extra["messages"]`, but a `LLMProvider` that doesn't
    know this module's `extra` convention still sees a real conversation
    instead of an empty list.

    Returns `None` if no provider is configured (§67 — the same honest
    no-provider fallback `structured_llm` uses) or the provider call itself
    raised (network/outage) — logged, not raised further. Bring your own
    history/retry/approval logic around this — see the module docstring
    for why that isn't this function's job.
    """
    llm = context.resources.llm
    if llm is None:
        return None
    extra: dict[str, Any] = {"messages": messages}
    payload_tools = tools_payload(tools)
    if payload_tools:
        extra["tools"] = payload_tools
        extra["tool_choice"] = "auto"
    typed_messages = [
        Message(
            role=cast(Role, m.get("role", "user")), content=str(m.get("content") or "")
        )
        for m in messages
    ]
    request = LLMRequest(
        messages=typed_messages,
        temperature=temperature,
        max_tokens=max_tokens,
        extra=extra,
    )
    try:
        return await llm.complete(request)
    except Exception as exc:
        logger.warning("native_tool_use: provider call failed: %r", exc)
        return None


__all__ = ["ToolCall", "native_complete", "parse_tool_calls", "tools_payload"]
