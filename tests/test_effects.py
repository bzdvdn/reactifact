"""effects (§24): produce-scoped effect slots, compiled by the runtime."""

import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Produce,
    ProduceCall,
    Runtime,
    RuntimeResources,
)
from reactifact.effects import Effects, Handle, current_effects
from reactifact.patches import Create, Link, Update


class Note(BaseModel):
    text: str


class Doc(BaseModel):
    text: str


def test_effects_create_pins_id_and_returns_handle():
    ctx = Context(resources=RuntimeResources())
    effects = Effects(ctx)
    handle = effects.create(Note(text="n"), id="note:1")
    assert isinstance(handle, Handle)
    assert handle.id == "note:1"
    assert handle.type == "Note"
    creates = [op for op in effects.operations if isinstance(op, Create)]
    assert creates and creates[0].id == "note:1"


def test_effects_create_generates_id_when_omitted():
    effects = Effects(Context(resources=RuntimeResources()))
    handle = effects.create(Note(text="x"))
    assert handle.id.startswith("note:")
    assert any(
        isinstance(op, Create) and op.id == handle.id for op in effects.operations
    )


def test_handle_link_resolves_artifact_and_handle_targets():
    effects = Effects(Context(resources=RuntimeResources()))
    doc = effects.create(Doc(text="d"), id="doc:1")
    note = effects.create(Note(text="n"), id="note:1")
    note.link("supported_by", doc)  # Handle target

    class Other(BaseModel):
        pass

    # an existing artifact as a target
    real = Context(resources=RuntimeResources()).create(Other())
    note.link("extracted_from", real)
    links = [op for op in effects.operations if isinstance(op, Link)]
    assert links[0].artifact_id == "note:1"
    assert links[0].relation == "supported_by"
    assert links[0].target_id == "doc:1"
    assert links[1].target_id == real.id


def test_effects_update_bumps_existing_artifact():
    ctx = Context(resources=RuntimeResources())
    note = ctx.create(Note(text="v1"), id="note:1")
    effects = Effects(ctx)
    effects.update(note, text="v2")
    updates = [op for op in effects.operations if isinstance(op, Update)]
    assert len(updates) == 1
    assert updates[0].artifact_id == "note:1"
    assert updates[0].new_data.text == "v2"


def test_create_once_creates_when_absent():
    ctx = Context(resources=RuntimeResources())
    effects = Effects(ctx)
    handle = effects.create_once(Note(text="v1"), id="note:1")
    assert handle is not None
    assert handle.id == "note:1"
    creates = [op for op in effects.operations if isinstance(op, Create)]
    assert len(creates) == 1


def test_create_once_is_none_when_already_present():
    ctx = Context(resources=RuntimeResources())
    ctx.create(Note(text="v1"), id="note:1")
    effects = Effects(ctx)
    handle = effects.create_once(Note(text="v2"), id="note:1")
    assert handle is None
    assert effects.operations == []


def test_effects_upsert_is_explicit_create_or_refresh():
    """`upsert` compiles to the same Create op as `create(..., id=...)` — it
    only makes the "may already exist" intent explicit at the call site."""
    effects = Effects(Context(resources=RuntimeResources()))
    handle = effects.upsert(Note(text="v2"), id="note:1")
    assert handle.id == "note:1"
    creates = [op for op in effects.operations if isinstance(op, Create)]
    assert len(creates) == 1
    assert creates[0].id == "note:1"


def test_upsert_refreshes_existing_artifact_via_runtime():
    class Refresh(Produce[Note]):
        artifact_type = Note

        async def produce(self, call: ProduceCall):
            self.effects.upsert(Note(text="v2"), id="note:1")
            return None

    class Refresher(Agent):
        consumes = [Consume(Doc)]
        produces = [Refresh()]

    ctx = Context(resources=RuntimeResources())
    ctx.create(Note(text="v1"), id="note:1")
    runtime = Runtime(ctx, agents=[Refresher()])
    ctx.create(Doc(text="trigger"), id="doc:1")
    asyncio.run(runtime.arun())

    note = ctx.get("note:1")
    assert note is not None
    assert note.data.text == "v2"
    assert note.version == 1  # refreshed, not duplicated


def test_current_effects_none_outside_produce():
    assert current_effects() is None


def test_produce_effects_raises_outside_runtime():
    try:
        _ = Greeting().effects
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError outside a produce")


def test_effects_resume_answers_pending_question():
    from reactifact import PendingQuestion
    from reactifact.effects import current_effects

    class Resumer(Agent):
        consumes = [Consume(Doc)]

        async def run(self, event, context):
            question = context.list_artifacts(PendingQuestion)[0]
            current_effects().resume(question, "да")
            return None

    ctx = Context(resources=RuntimeResources())
    question = ctx.create(PendingQuestion(question="Approve?", kind="approval"))
    runtime = Runtime(ctx, agents=[Resumer()])
    ctx.create(Doc(text="trigger"), id="doc:1")
    asyncio.run(runtime.arun())

    updated = ctx.get(question.id)
    assert updated is not None
    assert updated.data.answered is True
    assert updated.data.resolution == "да"
    assert updated.data.resolved_at is not None


class Greeting(Produce[Note]):
    artifact_type = Note

    async def produce(self, call: ProduceCall):
        trigger = call.trigger
        note = self.effects.create(Note(text="hi"), id="note:1")
        note.link("supported_by", trigger)  # trigger: Doc artifact
        return None


class Greeter(Agent):
    consumes = [Consume(Doc)]
    produces = [Greeting()]


def test_runtime_compiles_effects_into_one_commit():
    ctx = Context(resources=RuntimeResources())
    runtime = Runtime(ctx, agents=[Greeter()])
    ctx.create(Doc(text="trigger"), id="doc:1")
    asyncio.run(runtime.arun())

    assert ctx.get("note:1") is not None
    linked = ctx.related("note:1", relation="supported_by")
    assert linked and linked[0].id == "doc:1"
