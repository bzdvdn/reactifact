"""Return-style Emit/Ensure/Edit/Drop/Relate keep mutations out of Effects."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Artifact,
    Consume,
    Context,
    Drop,
    Edit,
    Emit,
    Ensure,
    Pipeline,
    Relate,
    Runtime,
    step,
)


class Item(BaseModel):
    text: str


class Tag(BaseModel):
    name: str


@step(produces=Item, consumes=Item)
def rename(item: Artifact[Item]) -> Edit:
    return Edit(item, text=item.data.text.upper())


@step(produces=Item, consumes=Item)
def remove(item: Artifact[Item]) -> Drop:
    return Drop(item)


@step(produces=Tag, consumes=(Item, Consume(Tag, wakes=False)))
def tag(item: Artifact[Item], tag: Artifact[Tag]) -> Relate:
    return Relate(item, "tagged_with", tag)


def test_edit_updates_fields():
    result = Pipeline(steps=[rename]).run(Item(text="hello"))
    item = result.context.latest(Item)
    assert item is not None
    assert item.data.text == "HELLO"


def test_drop_deletes_the_artifact():
    result = Pipeline(steps=[remove]).run(Item(text="bye"))
    assert result.context.latest(Item) is None


def test_relate_creates_a_provenance_edge():
    context = Context()
    context.create(Item(text="x"), id="item")
    context.create(Tag(name="prod"), id="tag")
    Runtime(context, steps=[tag]).run()
    assert [a.data.name for a in context.related("item", "tagged_with")] == ["prod"]


class Seed(BaseModel):
    text: str


@step(produces=Item, consumes=Seed)
def ensure_item(seed: Seed) -> Ensure:
    return Ensure(Item(text="ensured"), id="item:1")


def test_ensure_creates_only_once():
    context = Context()
    runtime = Runtime(context, steps=[ensure_item])
    context.create(Seed(text="a"), id="seed:1")
    asyncio.run(runtime.arun())

    first = context.latest(Item)
    assert first is not None
    assert first.data.text == "ensured"
    version = first.version

    context.create(Seed(text="b"), id="seed:2")
    asyncio.run(runtime.arun())

    items = context.list_artifacts(Item)
    assert len(items) == 1
    assert items[0].version == version  # no refresh, unlike Emit


@step(produces=Item, consumes=Seed)
def ensure_from(seed: Artifact[Seed]) -> Ensure:
    return Ensure(Item(text=seed.data.text), source=seed)


def test_ensure_derives_the_id_from_source():
    context = Context()
    context.create(Seed(text="hello"), id="seed:1")
    asyncio.run(Runtime(context, steps=[ensure_from]).arun())

    item = context.latest(Item)
    assert item is not None
    assert item.id == "item:seed:1"
    assert item.data.text == "hello"


@step(produces=Tag, consumes=Seed)
def ensure_tag(seed: Artifact[Seed]) -> Ensure:
    return Ensure(
        Tag(name="prod"),
        id="tag:1",
        links=(("tagged_with", seed),),
    )


def test_ensure_applies_links_only_when_creating():
    context = Context()
    context.create(Seed(text="x"), id="seed:1")
    asyncio.run(Runtime(context, steps=[ensure_tag]).arun())

    assert [tag.data.text for tag in context.related("tag:1", "tagged_with")] == ["x"]


def test_ensure_requires_exactly_one_id_or_source():
    with pytest.raises(ValueError, match="exactly one"):
        Ensure(Item(text="x"))
    with pytest.raises(ValueError, match="exactly one"):
        Ensure(Item(text="x"), id="item:1", source=Seed(text="s"))


def test_emit_with_an_existing_id_refreshes():
    @step(produces=Item, consumes=Seed)
    def emit_item(seed: Seed) -> Emit:
        return Emit(Item(text=seed.text), id="item:1")

    context = Context()
    runtime = Runtime(context, steps=[emit_item])
    context.create(Seed(text="a"), id="seed:1")
    asyncio.run(runtime.arun())
    first = context.latest(Item)
    assert first is not None
    version = first.version

    context.create(Seed(text="b"), id="seed:2")
    asyncio.run(runtime.arun())

    updated = context.latest(Item)
    assert updated is not None
    assert updated.data.text == "b"
    assert updated.version > version
