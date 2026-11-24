"""Return-style Edit/Drop/Relate keep simple mutations out of Effects."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import (
    Artifact,
    Consume,
    Context,
    Drop,
    Edit,
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
