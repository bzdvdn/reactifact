"""`StagedPipeline`: ordered stages, reactive within each stage, opt-in re-entry."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Chat,
    Consume,
    Context,
    Pipeline,
    PipelineRunner,
    ReactiveSchedule,
    RunConfig,
    RunOutcome,
    Runtime,
    RuntimeResources,
    Stage,
    StagedPipeline,
    StagedSchedule,
    Step,
)
from reactifact.checkpoints import InMemoryKVBackend
from reactifact.session import SessionStore


class Seed(BaseModel):
    text: str


class A(BaseModel):
    text: str


class B(BaseModel):
    text: str


class C(BaseModel):
    text: str


class MakeA(Step[A]):
    reads = (Seed,)

    async def run(self, seed: Seed) -> A:
        return A(text=f"a:{seed.text}")


class MakeB(Step[B]):
    reads = (A,)

    async def run(self, a: A) -> B:
        return B(text=f"b:{a.text}")


class MakeC(Step[C]):
    reads = (B,)

    async def run(self, b: B) -> C:
        return C(text=f"c:{b.text}")


def _pipeline(stages: list[Stage]) -> StagedPipeline:
    return StagedPipeline(stages, seed_type=Seed, answer_type=C)


def test_staged_pipeline_flattens_agents_and_uses_a_staged_schedule():
    a = Agent("a", consumes=[Consume(Seed)], steps=[MakeA()])
    b = Agent("b", consumes=[Consume(A)], steps=[MakeB()])
    pipeline = _pipeline(
        [
            Stage("one", [a]),
            Stage("two", [b]),
        ]
    )

    assert pipeline.agents == [a, b]
    assert isinstance(pipeline.schedule(), StagedSchedule)
    assert isinstance(Pipeline(agents=[a, b]).schedule(), ReactiveSchedule)


def test_staged_runner_runs_each_stage_to_completion():
    order: list[str] = []

    class TrackA(MakeA):
        async def run(self, seed: Seed) -> A:
            order.append("a")
            return await super().run(seed)

    class TrackB(MakeB):
        async def run(self, a: A) -> B:
            order.append("b")
            return await super().run(a)

    class TrackC(MakeC):
        async def run(self, b: B) -> C:
            order.append("c")
            return await super().run(b)

    pipeline = _pipeline(
        [
            Stage("one", [Agent("a", consumes=[Consume(Seed)], steps=[TrackA()])]),
            Stage("two", [Agent("b", consumes=[Consume(A)], steps=[TrackB()])]),
            Stage("three", [Agent("c", consumes=[Consume(B)], steps=[TrackC()])]),
        ]
    )
    runner = PipelineRunner(pipeline, config=RunConfig(resources=RuntimeResources()))
    result = runner.run(Seed(text="x"))

    assert order == ["a", "b", "c"]
    assert result.context.latest(C).data.text == "c:b:a:x"


def test_later_stage_sees_an_artifact_whose_event_was_consumed_earlier():
    """The wake pass covers artifacts created before the stage started."""

    class AlsoConsumesA(Step[C]):
        reads = (A,)

        async def run(self, a: A) -> C:
            return C(text=f"early:{a.text}")

    pipeline = _pipeline(
        [
            Stage(
                "one",
                [Agent("a", consumes=[Consume(Seed)], steps=[MakeA()])],
            ),
            Stage(
                "two",
                [
                    Agent(
                        "reader",
                        consumes=[Consume(A)],
                        steps=[AlsoConsumesA()],
                    ),
                    Agent("b", consumes=[Consume(A)], steps=[MakeB()]),
                ],
            ),
        ]
    )
    runner = PipelineRunner(pipeline, config=RunConfig(resources=RuntimeResources()))
    result = runner.run(Seed(text="x"))

    assert result.context.latest(B).data.text == "b:a:x"
    assert result.context.latest(C).data.text == "early:a:x"


def test_reopen_lets_a_later_stage_feed_an_earlier_one():
    class FeedBack(Step[C]):
        reads = (B,)

        async def run(self, b: B) -> C:
            return C(text=f"reopened:{b.text}")

    pipeline = _pipeline(
        [
            Stage(
                "one",
                [
                    Agent("a", consumes=[Consume(Seed)], steps=[MakeA()]),
                    Agent("feedback", consumes=[Consume(B)], steps=[FeedBack()]),
                ],
                reopen=True,
            ),
            Stage("two", [Agent("b", consumes=[Consume(A)], steps=[MakeB()])]),
        ]
    )
    runner = PipelineRunner(pipeline, config=RunConfig(resources=RuntimeResources()))
    result = runner.run(Seed(text="x"))

    assert result.context.latest(C).data.text == "reopened:b:a:x"


def test_chat_uses_the_pipeline_schedule():
    order: list[str] = []

    class TrackA(MakeA):
        async def run(self, seed: Seed) -> A:
            order.append("a")
            return await super().run(seed)

    class TrackB(MakeB):
        async def run(self, a: A) -> B:
            order.append("b")
            return await super().run(a)

    pipeline = StagedPipeline(
        [
            Stage("one", [Agent("a", consumes=[Consume(Seed)], steps=[TrackA()])]),
            Stage("two", [Agent("b", consumes=[Consume(A)], steps=[TrackB()])]),
        ],
        seed_type=Seed,
        answer_type=B,
    )
    chat = Chat(
        pipeline=pipeline,
        store=SessionStore(InMemoryKVBackend()),
        reply=lambda ctx, mid: {
            "reply": ctx.latest(B).data.text if ctx.latest(B) else ""
        },
        config=RunConfig(resources=RuntimeResources()),
    )
    payload = asyncio.run(chat.invoke("hello", session_id="staged-chat"))

    assert order == ["a", "b"]
    assert payload["reply"] == "b:a:hello"


def test_staged_pipeline_requires_stages():
    try:
        StagedPipeline([])
    except TypeError as exc:
        assert "stages" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected TypeError")


def test_stage_requires_agents():
    try:
        Stage("empty", [])
    except ValueError as exc:
        assert "agent" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_staged_stop_reports_stopped() -> None:
    stage = Stage("make", [Agent("a", consumes=[Consume(Seed)], steps=[MakeA()])])
    runtime = Runtime(
        Context(),
        agents=list(stage.agents),
        schedule=StagedSchedule([stage]),
    )
    runtime.request_stop()

    asyncio.run(runtime.arun())

    assert runtime.outcome is RunOutcome.STOPPED
