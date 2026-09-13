import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Produce,
    Runtime,
    produce,
)


class Input(BaseModel):
    text: str


class Marker(BaseModel):
    value: str


# --- Style 1: subclass of Produce (logic next to the type) ---


class MarkerProduce(Produce):
    artifact_type = Marker

    async def produce(self, context, inputs, event=None) -> None:
        artifact = context.get(event.artifact_id) if event is not None else None
        if artifact is None or not isinstance(artifact.data, Input):
            return None
        self.effects.create(Marker(value=artifact.data.text.upper()))
        return None


class SubclassAgent(Agent):
    consumes = [Consume(Input)]
    produces = [MarkerProduce()]


def test_produce_subclass_style():
    ctx = Context()
    runtime = Runtime(ctx, agents=[SubclassAgent()])
    ctx.create(Input(text="hi"))
    asyncio.run(runtime.arun())

    markers = ctx.list_artifacts(Marker)
    assert len(markers) == 1
    assert markers[0].data.value == "HI"


# --- Style 2: the @produce decorator (compact factory) ---


@produce(Marker)
async def decorator_factory(context, inputs, event):
    artifact = context.get(event.artifact_id) if event is not None else None
    if artifact is None or not isinstance(artifact.data, Input):
        return None
    return Marker(value=artifact.data.text.capitalize())


class DecoratorAgent(Agent):
    consumes = [Consume(Input)]
    produces = [decorator_factory]


def test_produce_decorator_style():
    ctx = Context()
    runtime = Runtime(ctx, agents=[DecoratorAgent()])
    ctx.create(Input(text="привет"))
    asyncio.run(runtime.arun())

    markers = ctx.list_artifacts(Marker)
    assert len(markers) == 1
    assert markers[0].data.value == "Привет"


# --- Style 2b: the @produce decorator with an effects slot (like self.effects) ---


@produce(Marker)
async def decorator_effects(context, inputs, event, effects):
    artifact = context.get(event.artifact_id) if event is not None else None
    if artifact is None or not isinstance(artifact.data, Input):
        return None
    handle = effects.create(Marker(value=artifact.data.text.upper()))
    effects.link(handle, "derived_from", artifact)
    return None


class DecoratorEffectsAgent(Agent):
    consumes = [Consume(Input)]
    produces = [decorator_effects]


def test_produce_decorator_effects_style():
    ctx = Context()
    runtime = Runtime(ctx, agents=[DecoratorEffectsAgent()])
    artifact = ctx.create(Input(text="world"))
    asyncio.run(runtime.arun())

    markers = ctx.list_artifacts(Marker)
    assert len(markers) == 1
    assert markers[0].data.value == "WORLD"
    relations = [r for r in ctx.relations() if r.relation == "derived_from"]
    assert len(relations) == 1
    assert relations[0].source_id == markers[0].id
    assert relations[0].target_id == artifact.id


# --- Style 2c: effects slot also covers update/ask (full self.effects surface) ---


class Task(BaseModel):
    status: str


@produce(Task)
async def decorator_update(context, inputs, event, effects):
    existing = next((a for a in inputs if isinstance(a.data, Task)), None)
    if existing is not None:
        effects.update(existing, status="done")
        return None
    return None


class DecoratorUpdateAgent(Agent):
    consumes = [Consume(Task)]
    produces = [decorator_update]


def test_produce_decorator_update_through_slot():
    ctx = Context()
    runtime = Runtime(ctx, agents=[DecoratorUpdateAgent()])
    ctx.create(Task(status="new"))
    asyncio.run(runtime.arun())

    tasks = ctx.list_artifacts(Task)
    assert len(tasks) == 1
    assert tasks[0].data.status == "done"


# --- Two-argument factory kwarg: removed for 1.0, @produce is the only way ---


def plain_factory(context, inputs):
    if not inputs:
        return None
    return Marker(value=inputs[0].data.text + "!")


def test_plain_two_arg_factory_kwarg_was_removed():
    with pytest.raises(TypeError):
        Produce(Marker, factory=plain_factory)  # type: ignore[call-arg]


def test_produce_decorator_does_not_warn(recwarn):
    """The @produce decorator never touches the deprecated factory= path —
    locks in that the canonical style stays warning-free."""

    @produce(Marker)
    async def _f(context, inputs, effects):
        return None

    assert len(recwarn) == 0


# --- The event reaches produce ---

received = {}


class RecordProduce(Produce):
    artifact_type = Marker

    async def produce(self, context, inputs, event=None):
        received["id"] = event.artifact_id if event is not None else None
        self.effects.create(Marker(value="recorded"))
        return None


class RecordAgent(Agent):
    consumes = [Consume(Input)]
    produces = [RecordProduce()]


def test_event_reaches_produce():
    ctx = Context()
    runtime = Runtime(ctx, agents=[RecordAgent()])
    artifact = ctx.create(Input(text="x"))
    asyncio.run(runtime.arun())

    assert received["id"] == artifact.id
    assert len(ctx.list_artifacts(Marker)) == 1


def test_artifact_type_auto_derived_from_generic():
    class Auto(Produce[Marker]):
        async def produce(self, context, inputs, event=None):
            return None

    assert Auto.artifact_type is Marker
    instance = Auto()
    assert instance.artifact_type is Marker
