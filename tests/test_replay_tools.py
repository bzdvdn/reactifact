"""Record/replay tool calls and Runtime.replay_run (LLM + tools)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Agent, Context, Runtime, RuntimeResources, Step, tool
from reactifact.providers import FakeLLM, LLMRequest, Message
from reactifact.replay import (
    RecordingToolRecorder,
    ReplayLLM,
    ReplayMiss,
    ReplayToolRecorder,
)
from reactifact.tools import begin_tool_runs, end_tool_runs, execute_tool
from reactifact.tracing import Tracer


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@tool
async def echo(text: str) -> str:
    "Echo the input."
    return text


def test_execute_tool_records_and_replays(tmp_path):
    recording = str(tmp_path / "tools.jsonl")

    async def go():
        recorder = RecordingToolRecorder(recording)
        token = begin_tool_runs(recorder=recorder)
        try:
            first = await execute_tool(echo, {"text": "hi"})
        finally:
            end_tool_runs(token)

        replay = ReplayToolRecorder(recording)
        token = begin_tool_runs(recorder=replay)
        try:
            second = await execute_tool(echo, {"text": "hi"})
        finally:
            end_tool_runs(token)
        return first, second

    first, second = asyncio.run(go())
    assert first.text == "hi"
    assert second.text == "hi"


def test_replay_tool_miss_is_explicit(tmp_path):
    async def go():
        replay = ReplayToolRecorder(str(tmp_path / "missing.jsonl"))
        token = begin_tool_runs(recorder=replay)
        try:
            await execute_tool(echo, {"text": "unrecorded"})
        finally:
            end_tool_runs(token)

    with pytest.raises(ReplayMiss, match="not recorded"):
        asyncio.run(go())


class ToolStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, question: Question) -> Answer:
        output = await execute_tool(echo, {"text": question.text})
        return Answer(text=output.text)


def test_runtime_replay_run_replays_tools(tmp_path):
    recording = str(tmp_path / "tools.jsonl")

    async def record():
        context = Context(
            resources=RuntimeResources(tool_recorder=RecordingToolRecorder(recording))
        )
        context.create(Question(text="tool"), id="question:1")
        runtime = Runtime(context, groups=[Agent("agent", steps=[ToolStep()])])
        await runtime.arun()
        return context.list_artifacts(Answer)[0].data.text

    async def replay():
        context = Context()
        context.create(Question(text="tool"), id="question:1")
        runtime = Runtime(context, groups=[Agent("agent", steps=[ToolStep()])])
        await runtime.replay_run(tool_recording=recording)
        return context.list_artifacts(Answer)[0].data.text

    assert asyncio.run(record()) == "tool"
    assert asyncio.run(replay()) == "tool"


class LLMStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, context: Context, question: Question) -> Answer:
        llm = context.resources.llm
        assert llm is not None
        response = await llm.complete(
            LLMRequest(messages=[Message.user(question.text)])
        )
        return Answer(text=response.text)


def test_runtime_replay_run_replays_llm(tmp_path):
    recording = str(tmp_path / "llm.jsonl")

    async def record():
        context = Context(
            resources=RuntimeResources(
                llm=ReplayLLM(
                    recording,
                    mode="record",
                    inner=FakeLLM('{"text": "recorded"}'),
                    model="fake",
                )
            )
        )
        context.create(Question(text="question"), id="question:1")
        runtime = Runtime(context, groups=[Agent("agent", steps=[LLMStep()])])
        await runtime.arun()
        return context.list_artifacts(Answer)[0].data.text

    async def replay():
        context = Context(resources=RuntimeResources(llm=FakeLLM("live")))
        context.create(Question(text="question"), id="question:1")
        runtime = Runtime(context, groups=[Agent("agent", steps=[LLMStep()])])
        await runtime.replay_run(llm_recording=recording, model="fake")
        return context.list_artifacts(Answer)[0].data.text

    assert asyncio.run(record()) == '{"text": "recorded"}'
    assert asyncio.run(replay()) == '{"text": "recorded"}'


def test_runtime_replay_run_requires_a_recording():
    runtime = Runtime(Context(), groups=[])

    with pytest.raises(ValueError, match="llm_recording"):
        asyncio.run(runtime.replay_run())


class SpanTracer(Tracer):
    def __init__(self):
        super().__init__()
        self.spans: list = []

    def on_span(self, span):
        self.spans.append(span)


class DirectStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, question: Question) -> Answer:
        output = await echo.execute({"text": question.text})
        return Answer(text=output.text)


def test_direct_tool_execute_is_traced():
    context = Context()
    context.create(Question(text="direct"), id="question:1")
    tracer = SpanTracer()
    runtime = Runtime(
        context,
        groups=[Agent("agent", steps=[DirectStep()])],
        tracer=tracer,
    )

    asyncio.run(runtime.arun())

    assert len(tracer.spans) == 1
    tools = tracer.spans[0].tools
    assert [run.tool for run in tools] == ["echo"]
    assert tools[0].ok is True


def test_execute_tool_does_not_double_trace():
    context = Context()
    context.create(Question(text="tool"), id="question:1")
    tracer = SpanTracer()
    runtime = Runtime(
        context,
        groups=[Agent("agent", steps=[ToolStep()])],
        tracer=tracer,
    )

    asyncio.run(runtime.arun())

    assert len(tracer.spans) == 1
    assert len(tracer.spans[0].tools) == 1


def test_direct_tool_execute_records_and_replays(tmp_path):
    recording = str(tmp_path / "direct-tools.jsonl")

    async def record():
        context = Context(
            resources=RuntimeResources(tool_recorder=RecordingToolRecorder(recording))
        )
        context.create(Question(text="direct"), id="question:1")
        runtime = Runtime(context, groups=[Agent("agent", steps=[DirectStep()])])
        await runtime.arun()
        return context.list_artifacts(Answer)[0].data.text

    async def replay():
        context = Context()
        context.create(Question(text="direct"), id="question:1")
        runtime = Runtime(context, groups=[Agent("agent", steps=[DirectStep()])])
        await runtime.replay_run(tool_recording=recording)
        return context.list_artifacts(Answer)[0].data.text

    assert asyncio.run(record()) == "direct"
    assert asyncio.run(replay()) == "direct"
