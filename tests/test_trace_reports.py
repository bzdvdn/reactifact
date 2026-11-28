"""Portable trace reports, trace diff, and Pipeline trace integration."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import Pipeline, PipelineRunner, RunConfig, step
from reactifact.tracing import (
    AgentSpan,
    ArtifactRef,
    LLMCall,
    RunTrace,
    StepRun,
    TraceStore,
)
from reactifact.tracing.diff import diff_traces


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(reads=Question)
def answer(question: Question) -> Answer:
    return Answer(text=question.text)


def _trace(run_id: str, *, outcome: str = "completed") -> RunTrace:
    return RunTrace(
        id=run_id,
        outcome=outcome,
        duration_ms=2.0,
        spans=[
            AgentSpan(
                agent="writer",
                event_artifact_type="Question",
                latency_ms=2.0,
                produces=[StepRun(name="answer", operations=1)],
                writes=[ArtifactRef(artifact_id="a", data_type="Answer")],
                llm_calls=[
                    LLMCall(
                        agent="writer",
                        step="answer",
                        model="fake",
                        prompt_tokens=3,
                        completion_tokens=4,
                        latency_ms=1.0,
                    )
                ],
            )
        ],
    )


def test_trace_to_text_markdown_and_html():
    trace = _trace("run-1")
    assert "trace run-1" in trace.to_text()
    assert "## Trace `run-1`" in trace.to_markdown()
    html = trace.to_html()
    assert "<!doctype html>" in html
    assert "writer" in html
    assert "Answer" in html


def test_trace_diff_reports_outcome_and_changes():
    left = _trace("left")
    right = _trace("right", outcome="failed")
    right.spans[0].agent = "other"
    diff = diff_traces(left, right)
    text = diff.to_text()
    assert "completed -> failed" in text
    assert "spans + other" in text
    assert "spans - writer" in text


def test_pipeline_traces_are_readable_from_the_result():
    runner = PipelineRunner(Pipeline(steps=[answer]), config=RunConfig(traces=True))
    result = runner.run(Question(text="hi"))
    assert result.trace_id
    trace = asyncio.run(result.trace())
    assert trace is not None
    assert trace.outcome == "completed"
    runner.close()


def test_cli_renders_text_and_diff(tmp_path):
    from reactifact.cli import main

    store = TraceStore(str(tmp_path / "t.db"))
    asyncio.run(store.export(_trace("left")))
    asyncio.run(store.export(_trace("right")))
    store.close()

    path = str(tmp_path / "t.db")
    assert main(["trace", path, "left", "--format", "text"]) == 0
    assert main(["trace", path, "left", "--against", "right"]) == 0
