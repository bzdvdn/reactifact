from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    EventType,
    Runtime,
    Step,
)


class Item(BaseModel):
    text: str


class Created(BaseModel):
    text: str


class Updated(BaseModel):
    text: str


class Deleted(BaseModel):
    text: str


class OnCreateStep(Step[Created]):
    reads = (Consume.on_create(Item),)

    async def run(self, item: Item) -> Created:
        return Created(text=item.text)


class OnUpdateStep(Step[Updated]):
    reads = (Consume.on_update(Item),)

    async def run(self, item: Item) -> Updated:
        return Updated(text=item.text)


class OnDeleteStep(Step[Deleted]):
    reads = (Consume.on_delete(Item),)

    async def run(self, event) -> Deleted:
        return Deleted(text=event.artifact_id)


def test_lifecycle_consume_event_types() -> None:
    assert list(Consume.on_create(Item).event_types) == [EventType.ARTIFACT_CREATED]
    assert list(Consume.on_update(Item).event_types) == [EventType.ARTIFACT_UPDATED]
    assert list(Consume.on_delete(Item).event_types) == [EventType.ARTIFACT_DELETED]
    assert list(Consume.on_stale(Item).event_types) == [EventType.ARTIFACT_STALE]


def test_on_create_and_on_update_are_scoped_to_their_event() -> None:
    context = Context()
    runtime = Runtime(context, steps=[OnCreateStep(), OnUpdateStep()])
    item = context.create(Item(text="first"))

    asyncio.run(runtime.arun())
    assert [artifact.data.text for artifact in context.list_artifacts(Created)] == [
        "first"
    ]
    assert context.list_artifacts(Updated) == []

    context.update(item.id, Item(text="second"))
    asyncio.run(runtime.arun())
    assert [artifact.data.text for artifact in context.list_artifacts(Updated)] == [
        "second"
    ]
    assert len(context.list_artifacts(Created)) == 1

    # Same data is a no-op: no update event, no second run.
    context.update(item.id, Item(text="second"))
    asyncio.run(runtime.arun())
    assert len(context.list_artifacts(Updated)) == 1


def test_on_delete_runs_with_the_event_and_without_data() -> None:
    context = Context()
    runtime = Runtime(context, steps=[OnDeleteStep()])
    item = context.create(Item(text="gone"))

    asyncio.run(runtime.arun())  # create does not match OnDelete

    assert context.delete(item.id)
    asyncio.run(runtime.arun())

    deleted = context.list_artifacts(Deleted)
    assert [artifact.data.text for artifact in deleted] == [item.id]
