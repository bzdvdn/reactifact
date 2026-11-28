"""Replay: record a tool call, then reproduce the run without executing it."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Runtime, RuntimeResources, Step, tool
from reactifact.replay import RecordingToolRecorder


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@tool
async def echo(text: str) -> str:
    "Echo the input; the call is recorded and replayed."
    return text


class EchoStep(Step):
    reads = (Question,)
    produces = (Answer,)

    async def run(self, question: Question) -> Answer:
        # A direct tool.execute is instrumented the same way as execute_tool.
        output = await echo.execute({"text": question.text})
        return Answer(text=output.text)


async def _run_replay(directory: Path) -> dict[str, Any]:
    recording = str(directory / "tools.jsonl")

    recorded_context = Context(
        resources=RuntimeResources(tool_recorder=RecordingToolRecorder(recording))
    )
    recorded_context.create(Question(text="replay me"), id="question:1")
    recorded_runtime = Runtime(
        recorded_context,
        agents=[Agent("agent", consumes=[Consume(Question)], steps=[EchoStep()])],
    )
    await recorded_runtime.arun()
    recorded = recorded_context.list_artifacts(Answer)[0].data.text

    replay_context = Context()
    replay_context.create(Question(text="replay me"), id="question:1")
    replay_runtime = Runtime(
        replay_context,
        agents=[Agent("agent", consumes=[Consume(Question)], steps=[EchoStep()])],
    )
    await replay_runtime.replay_run(tool_recording=recording)
    replayed = replay_context.list_artifacts(Answer)[0].data.text

    return {
        "recorded": recorded,
        "replayed": replayed,
        "same": recorded == replayed,
        "recording_lines": len(Path(recording).read_text().splitlines()),
    }


async def run_replay_scene() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        return await _run_replay(Path(tmp))
