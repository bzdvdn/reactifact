"""ToolUse native/auto modes and per-tool progress templates."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Runtime,
    RuntimeResources,
    tool,
)
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse, LLMResponseChunk
from reactifact.tools.use import NativeToolUse, ToolAnswer, ToolUse


class Question(BaseModel):
    text: str


class ScriptedNativeLLM(LLMProvider):
    def __init__(self, bodies: list[dict]):
        self.bodies = list(bodies)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = self.bodies.pop(0)
        message = (body.get("choices") or [{}])[0].get("message", {})
        return LLMResponse(text=message.get("content", "") or "", raw=body)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        yield LLMResponseChunk(text="")


def _tool_call(name: str, args: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "1",
                            "type": "function",
                            "function": {"name": name, "arguments": args},
                        }
                    ],
                }
            }
        ]
    }


def _answer(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


calls: list[tuple[str, str]] = []


@tool(progress="Looking up {q}")
async def search(q: str) -> str:
    """Search a service."""
    calls.append(("search", q))
    return f"found {q}"


def _run(tool_step: ToolUse, bodies: list[dict], question: Question) -> Context:
    context = Context(resources=RuntimeResources(llm=ScriptedNativeLLM(bodies)))
    context.create(question)
    group = Agent("tool", consumes=[Consume(Question)], steps=[tool_step])
    asyncio.run(Runtime(context, agents=[group]).arun())
    return context


def test_tool_use_native_mode_calls_tools_and_answers():
    calls.clear()
    step = ToolUse("use search", [search], mode="native", max_steps=2)
    context = _run(
        step,
        [_tool_call("search", '{"q": "a"}'), _answer("final")],
        Question(text="goal"),
    )
    assert calls == [("search", "a")]
    answer = context.latest(ToolAnswer)
    assert answer is not None
    assert answer.data.text == "final"


def test_native_tool_use_is_the_native_mode_sugar():
    step = NativeToolUse("use search", [search])
    assert step.mode == "native"


def test_native_tool_progress_template_is_announced():
    calls.clear()
    context = Context(
        resources=RuntimeResources(
            llm=ScriptedNativeLLM(
                [_tool_call("search", '{"q": "a"}'), _answer("final")]
            )
        )
    )
    context.create(Question(text="goal"))
    queue = context.subscribe()
    group = Agent(
        "tool",
        consumes=[Consume(Question)],
        steps=[ToolUse("use search", [search], mode="native")],
    )
    asyncio.run(Runtime(context, agents=[group]).arun())
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    context.unsubscribe(queue)
    assert any(event.message == "Looking up a" for event in events)


def test_deferred_groups_are_json_only():
    from reactifact.tools.use import DeferredToolGroup

    group = DeferredToolGroup(
        group_id="g",
        display_name="G",
        description="d",
        tool_names=[],
        loader=lambda: [],
    )
    try:
        ToolUse("x", [], mode="native", deferred_tool_groups=[group])
    except ValueError as exc:
        assert "JSON mode" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
