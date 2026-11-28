"""Typed run events: `Runtime.astream_events` and its stream modes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Runtime,
    RuntimeResources,
    Step,
)
from reactifact.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    Message,
)
from reactifact.streaming import (
    AgentEnd,
    AgentStart,
    ArtifactWrite,
    CustomEvent,
    GenerationStart,
    LLMDelta,
    RunEnd,
    RunFailed,
    RunStart,
    ToolEnd,
    ToolStart,
    Values,
)
from reactifact.tools import execute_tool, tool


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class StreamLLM(LLMProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text="hello", usage={"prompt_tokens": 1, "completion_tokens": 1}
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        yield LLMResponseChunk(text="he")
        yield LLMResponseChunk(
            text="llo", usage={"prompt_tokens": 1, "completion_tokens": 1}
        )


class StreamStep(Step):
    reads = (Question,)
    produces = (Answer,)

    async def run(self, context: Context, question: Question) -> Answer:
        llm = context.resources.llm
        assert llm is not None
        request = LLMRequest(messages=[Message.user(question.text)])
        text = "".join([chunk.text async for chunk in llm.stream(request)])
        return Answer(text=text)


class AnnounceStep(Step):
    reads = (Question,)
    produces = (Answer,)

    async def run(self, context: Context, question: Question) -> Answer:
        context.announce("Searching…", kind="status", source="test")
        return Answer(text=question.text)


@tool
async def echo(text: str) -> str:
    "Echo text."
    return text


class ToolStep(Step):
    reads = (Question,)
    produces = (Answer,)

    async def run(self, context: Context, question: Question) -> Answer:
        output = await execute_tool(echo, {"text": question.text})
        return Answer(text=output.text)


def _runtime(step: Step, *, llm: LLMProvider | None = None) -> Runtime:
    context = Context(resources=RuntimeResources(llm=llm))
    context.create(Question(text="hi"))
    return Runtime(context, agents=[Agent("agent", steps=[step])])


async def _collect(runtime: Runtime, **kwargs):
    return [event async for event in runtime.astream_events(**kwargs)]


def test_debug_messages_and_run_lifecycle():
    runtime = _runtime(StreamStep(), llm=StreamLLM())

    events = asyncio.run(_collect(runtime, stream_mode=["debug", "messages"]))

    assert isinstance(events[0], RunStart)
    assert isinstance(events[-1], RunEnd)
    assert events[-1].outcome == "completed"
    assert events[-1].runs == 1
    assert any(isinstance(event, GenerationStart) for event in events)
    assert any(isinstance(event, AgentStart) for event in events)
    assert any(isinstance(event, AgentEnd) for event in events)
    deltas = [event for event in events if isinstance(event, LLMDelta)]
    assert "".join(event.delta for event in deltas) == "hello"
    assert all(event.streamed for event in deltas)


def test_updates_and_values_modes():
    runtime = _runtime(AnnounceStep())

    events = asyncio.run(_collect(runtime, stream_mode=["updates", "values"]))

    writes = [event for event in events if isinstance(event, ArtifactWrite)]
    assert len(writes) == 1
    assert writes[0].writes[0]["artifact_id"]
    assert writes[0].writes[0]["op_type"] == "create"

    values = [event for event in events if isinstance(event, Values)]
    assert len(values) == 1
    added = values[0].diff["added"]
    assert len(added) == 1
    assert next(iter(added.values()))["text"] == "hi"


def test_custom_mode_forwards_announce():
    runtime = _runtime(AnnounceStep())

    events = asyncio.run(_collect(runtime, stream_mode="custom"))

    custom = [event for event in events if isinstance(event, CustomEvent)]
    assert len(custom) == 1
    assert custom[0].event.message == "Searching…"
    assert custom[0].event.data["source"] == "test"


def test_debug_includes_tool_lifecycle():
    runtime = _runtime(ToolStep())

    events = asyncio.run(_collect(runtime, stream_mode="debug"))

    starts = [event for event in events if isinstance(event, ToolStart)]
    ends = [event for event in events if isinstance(event, ToolEnd)]
    assert [event.tool for event in starts] == ["echo"]
    assert [event.tool for event in ends] == ["echo"]
    assert ends[0].ok is True


def test_run_failure_emits_terminal_event_and_reraises():
    class Boom(Agent):
        consumes = [Consume(Question)]

        async def run(self, event, context):
            raise RuntimeError("boom")

    runtime = _runtime(AnnounceStep())
    runtime.agents = [Boom()]

    events = []

    async def go():
        with pytest.raises(RuntimeError, match="boom"):
            async for event in runtime.astream_events(stream_mode="debug"):
                events.append(event)

    asyncio.run(go())

    assert events
    assert isinstance(events[0], RunStart)
    assert isinstance(events[-1], RunFailed)
    assert "RuntimeError" in events[-1].error


def test_unknown_stream_mode_is_rejected():
    runtime = _runtime(AnnounceStep())

    async def go():
        with pytest.raises(ValueError, match="unknown stream_mode"):
            async for _ in runtime.astream_events(stream_mode="nope"):
                pass

    asyncio.run(go())
