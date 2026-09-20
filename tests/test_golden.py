"""reactifact.testing golden runs: freeze state + prompt hashes, assert re-runs."""

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Consume, Context, Runtime, create_agent, produce
from reactifact.testing import (
    AssertionFailure,
    assert_golden,
    capture,
    prompt_hashes,
    replay_resources,
)
from reactifact.tracing.models import AgentSpan, LLMCall, RunTrace


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@produce(Answer)
async def answer(call):
    question = call.trigger
    if question is None or not isinstance(question.data, Question):
        return None
    call.effects.create(Answer(text="A"), id=f"answer:{question.id}")
    return None


def _run() -> Context:
    context = Context()
    runtime = Runtime(
        context,
        agents=[create_agent("a", consumes=[Consume(Question)], produces=[answer])],
    )
    context.create(Question(text="q"), id="q")
    asyncio.run(runtime.arun())
    return context


def test_capture_and_assert_golden_roundtrip():
    golden = capture(_run())
    assert_golden(_run(), golden)  # deterministic run -> same fingerprint


def test_golden_detects_state_drift():
    golden = capture(_run())
    context = _run()
    artifact = context.latest(Answer)
    assert artifact is not None
    context.update(artifact.id, Answer(text="changed"))

    with pytest.raises(AssertionFailure, match="context hash drifted"):
        assert_golden(context, golden)


def test_prompt_hashes_extraction_and_drift():
    trace = RunTrace(
        id="r",
        spans=[
            AgentSpan(
                agent="a",
                llm_calls=[LLMCall(prompt_hash="h1"), LLMCall()],  # empty skipped
            )
        ],
    )
    assert prompt_hashes(trace) == ["h1"]

    golden = capture(_run(), trace=trace)
    assert golden.prompt_hashes == ("h1",)
    assert_golden(_run(), golden, trace=trace)

    drifted = RunTrace(
        id="r2", spans=[AgentSpan(agent="a", llm_calls=[LLMCall(prompt_hash="h2")])]
    )
    with pytest.raises(AssertionFailure, match="prompt hashes drifted"):
        assert_golden(_run(), golden, trace=drifted)


def test_replay_resources_installs_a_replay_llm(tmp_path):
    resources = replay_resources(tmp_path / "calls.jsonl")
    assert type(resources.llm).__name__ == "ReplayLLM"
