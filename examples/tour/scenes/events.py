"""Typed RunEvents: one run, every stream mode, no server and no API key."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel
from reactifact import (
    Context,
    Runtime,
    RuntimeResources,
    Step,
    create_agent,
    tool,
)
from reactifact.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    Message,
)
from reactifact.streaming import (
    ArtifactWrite,
    CustomEvent,
    LLMDelta,
    RunEnd,
    RunStart,
    ToolStart,
    Values,
)
from reactifact.tools import execute_tool


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class EchoLLM(LLMProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="echo")

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        yield LLMResponseChunk(text="ec")
        yield LLMResponseChunk(text="ho")


@tool
async def echo_tool(text: str) -> str:
    "Echo the input; a stand-in for a real tool call."
    return text


class EchoStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, context: Context, question: Question) -> Answer:
        context.announce("calling the model…", kind="status", source="tour")
        await execute_tool(echo_tool, {"text": question.text})
        llm = context.resources.llm
        assert llm is not None
        text = "".join(
            [
                chunk.text
                async for chunk in llm.stream(
                    LLMRequest(messages=[Message.user(question.text)])
                )
            ]
        )
        return Answer(text=text)


async def run_events() -> dict[str, Any]:
    context = Context(resources=RuntimeResources(llm=EchoLLM()))
    context.create(Question(text="hi"), id="question:1")
    runtime = Runtime(
        context,
        agents=[create_agent("events", steps=[EchoStep()])],
    )

    events = [
        event
        async for event in runtime.astream_events(
            stream_mode=["custom", "updates", "values", "debug", "messages"]
        )
    ]

    deltas = [event for event in events if isinstance(event, LLMDelta)]
    writes = [event for event in events if isinstance(event, ArtifactWrite)]
    values = [event for event in events if isinstance(event, Values)]
    custom = [event for event in events if isinstance(event, CustomEvent)]
    end = next(event for event in events if isinstance(event, RunEnd))
    return {
        "first": events[0].kind,
        "last": events[-1].kind,
        "kinds": sorted({event.kind for event in events}),
        "delta": "".join(event.delta for event in deltas),
        "streamed": all(event.streamed for event in deltas),
        "writes": len(writes),
        "value_added": len(values[0].diff.get("added", {})) if values else 0,
        "custom": custom[0].event.message if custom else "",
        "outcome": end.outcome,
        "has_start": isinstance(events[0], RunStart),
        "has_tool_start": any(isinstance(event, ToolStart) for event in events),
        "tool_starts": sum(isinstance(event, ToolStart) for event in events),
    }
