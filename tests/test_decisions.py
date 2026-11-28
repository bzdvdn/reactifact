"""Decision traces: guard/missing-input/stage/event-unmatched explanations."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Guard,
    Runtime,
    RuntimeResources,
    Stage,
    Step,
)
from reactifact.pipeline.schedule import StagedSchedule
from reactifact.tracing import Decision, RunTrace, Tracer, TraceStore


class CaptureSink:
    def __init__(self) -> None:
        self.traces: list[RunTrace] = []

    async def export(self, trace: RunTrace) -> None:
        self.traces.append(trace)


class Turn(BaseModel):
    query_id: str
    status: str = "researching"


class Evidence(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


def _tracer() -> tuple[Tracer, CaptureSink]:
    sink = CaptureSink()
    return Tracer(sinks=[sink]), sink


def test_guard_denial_is_recorded_as_a_decision():
    class Guarded(Step[Answer]):
        reads = (Turn,)
        guard = Guard.field("status", "answerable")

        async def run(self, turn: Turn) -> Answer:
            return Answer(text="never")

    tracer, sink = _tracer()
    context = Context(resources=RuntimeResources())
    context.create(Turn(query_id="q1", status="researching"))
    runtime = Runtime(
        context,
        agents=[Agent("answer", consumes=[Consume(Turn)], steps=[Guarded()])],
        tracer=tracer,
    )

    asyncio.run(runtime.arun())

    decisions = sink.traces[-1].decisions
    skipped = [d for d in decisions if d.code == "skipped_guard"]
    assert len(skipped) == 1
    assert skipped[0].agent == "answer"
    assert skipped[0].step == "Guarded"
    assert skipped[0].detail["phase"] == "pre"
    assert skipped[0].event_artifact_type == "Turn"


def test_missing_input_is_recorded_as_a_decision():
    class NeedsEvidence(Step[Answer]):
        reads = (Turn, Evidence)

        async def run(self, turn: Turn, evidence: Evidence) -> Answer:
            return Answer(text=evidence.text)

    tracer, sink = _tracer()
    context = Context(resources=RuntimeResources())
    context.create(Turn(query_id="q1"))
    runtime = Runtime(
        context,
        agents=[Agent("answer", consumes=[Consume(Turn)], steps=[NeedsEvidence()])],
        tracer=tracer,
        isolate_errors=True,
    )

    asyncio.run(runtime.arun())

    decisions = sink.traces[-1].decisions
    missing = [d for d in decisions if d.code == "skipped_missing_input"]
    assert len(missing) == 1
    assert missing[0].agent == "answer"
    assert missing[0].detail["parameter"] == "evidence"
    assert "Evidence" in missing[0].detail["expected"]


def test_event_without_a_consumer_is_recorded():
    tracer, sink = _tracer()
    context = Context(resources=RuntimeResources())
    context.create(Turn(query_id="q1"))
    runtime = Runtime(context, agents=[], tracer=tracer)

    asyncio.run(runtime.arun())

    decisions = sink.traces[-1].decisions
    unmatched = [d for d in decisions if d.code == "event_unmatched"]
    assert len(unmatched) == 1
    assert unmatched[0].event_artifact_type == "Turn"


def test_stage_enter_and_settle_are_recorded():
    class MakeAnswer(Step[Answer]):
        reads = (Turn,)

        async def run(self, turn: Turn) -> Answer:
            return Answer(text=turn.query_id)

    tracer, sink = _tracer()
    context = Context(resources=RuntimeResources())
    context.create(Turn(query_id="q1"))
    runtime = Runtime(
        context,
        agents=[Agent("answer", consumes=[Consume(Turn)], steps=[MakeAnswer()])],
        tracer=tracer,
        schedule=StagedSchedule(
            [
                Stage(
                    "one",
                    [Agent("answer", consumes=[Consume(Turn)], steps=[MakeAnswer()])],
                )
            ]
        ),
    )

    asyncio.run(runtime.arun())

    decisions = sink.traces[-1].decisions
    assert any(
        d.code == "stage_enter" and d.detail["stage"] == "one" for d in decisions
    )
    assert any(
        d.code == "stage_settled" and d.detail["stage"] == "one" for d in decisions
    )


def test_decisions_round_trip_through_trace_store(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    trace = RunTrace(
        id="r-decisions",
        outcome="completed",
        decisions=[
            Decision(
                code="skipped_guard",
                agent="answer",
                step="Build",
                reason="target answer:1 already exists",
                detail={"phase": "pre"},
            )
        ],
    )
    asyncio.run(store.export(trace))
    loaded = asyncio.run(store.get("r-decisions"))
    store.close()

    assert loaded is not None
    assert len(loaded.decisions) == 1
    assert loaded.decisions[0].code == "skipped_guard"
    assert "target answer:1" in loaded.why_text()
    assert "skipped_guard" in loaded.to_markdown()
