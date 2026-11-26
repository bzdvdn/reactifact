import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    EventType,
    Patch,
    Runtime,
    Step,
    StepGroup,
    Trigger,
)


class Number(BaseModel):
    value: int


class Doubler(StepGroup):
    def __init__(self):
        super().__init__(
            name="doubler", triggers=[Trigger(EventType.ARTIFACT_CREATED, Number)]
        )

    async def run(self, event, context):
        artifact = context.get(event.artifact_id)
        if artifact is None:
            return None
        new_value = artifact.data.value * 2
        return Patch().update(artifact.id, Number(value=new_value))


class Logger(StepGroup):
    def __init__(self):
        super().__init__(
            name="logger", triggers=[Trigger(EventType.ARTIFACT_UPDATED, Number)]
        )

    async def run(self, event, context):
        artifact = context.get(event.artifact_id)
        if artifact:
            print(f"Number updated to {artifact.data.value}")
        return None


class ThresholdAgent(StepGroup):
    def __init__(self):
        super().__init__(
            name="threshold_agent",
            triggers=[
                Trigger(
                    EventType.ARTIFACT_CREATED,
                    Number,
                    condition=lambda art: art.data.value > 10,
                )
            ],
        )

    async def run(self, event, context):
        return Patch().create(Marker())


class Marker(BaseModel):
    pass


class AsyncDoubler(StepGroup):
    def __init__(self):
        super().__init__(
            name="async_doubler", triggers=[Trigger(EventType.ARTIFACT_CREATED, Number)]
        )

    async def run(self, event, context):
        await asyncio.sleep(0.01)  # simulating async work
        artifact = context.get(event.artifact_id)
        if artifact is None:
            return None
        return Patch().update(artifact.id, Number(value=artifact.data.value * 3))


def test_reactive_doubling():
    ws = Context()
    runtime = Runtime(ws, groups=[Doubler(), Logger()])
    ws.create(Number(value=5))
    total_runs = runtime.run()  # synchronous wrapper
    assert total_runs == 2
    artifacts = ws.list_artifacts(Number)
    assert len(artifacts) == 1
    assert artifacts[0].data.value == 10


def test_trigger_condition():
    ws = Context()
    runtime = Runtime(ws, groups=[ThresholdAgent()])

    ws.create(Number(value=5))
    total_runs = runtime.run()
    assert total_runs == 0
    assert len(ws.list_artifacts(Marker)) == 0

    ws.create(Number(value=20))
    total_runs = runtime.run()
    assert total_runs == 1
    assert len(ws.list_artifacts(Marker)) == 1


def test_async_group():
    ws = Context()
    runtime = Runtime(ws, groups=[AsyncDoubler()])
    ws.create(Number(value=7))
    total_runs = asyncio.run(runtime.arun())  # async run
    assert total_runs == 1
    num = ws.list_artifacts(Number)[0]
    assert num.data.value == 21


def test_produces_validation_rejects_unknown_type():
    ctx = Context()
    runtime = Runtime(ctx, groups=[])

    class WrongOutput(BaseModel):
        text: str

    class Input(BaseModel):
        text: str

    class Output(BaseModel):
        text: str

    class BadAgent(StepGroup):
        name = "bad_agent"
        consumes = [Consume(Input)]  # <-- now there are triggers
        produces = [Step(Output)]  # only Output declared

        async def run(self, event, context):
            return Patch().create(WrongOutput(text="wrong"))

    runtime.register(BadAgent())
    ctx.create(Input(text="hello"))

    with pytest.raises(ValueError):
        asyncio.run(runtime.arun())
