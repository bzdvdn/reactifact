"""Optimistic concurrency: expected_version checks and atomic conflicts."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Agent, ArtifactConflict, Consume, Context, Runtime
from reactifact.effects import Effects, current_effects
from reactifact.operations import Update, operation_from_dict


class Note(BaseModel):
    text: str


class Trigger(BaseModel):
    text: str


def test_context_update_checks_expected_version():
    context = Context()
    context.create(Note(text="v1"), id="note:1")

    context.update("note:1", Note(text="v2"), expected_version=0)

    with pytest.raises(ArtifactConflict) as caught:
        context.update("note:1", Note(text="v3"), expected_version=0)

    assert caught.value.artifact_id == "note:1"
    assert caught.value.expected == 0
    assert caught.value.actual == 1
    assert context.get("note:1").data.text == "v2"  # type: ignore[union-attr]


def test_effects_update_records_expected_version():
    context = Context()
    note = context.create(Note(text="v1"), id="note:1")
    effects = Effects(context)

    effects.update(note, expected_version=0, text="v2")

    update = next(op for op in effects.operations if isinstance(op, Update))
    assert update.expected_version == 0
    assert update.new_data.text == "v2"


def test_update_operation_roundtrips_expected_version():
    operation = Update("note:1", Note(text="v2"), expected_version=3)

    restored = operation_from_dict(operation.to_dict())

    assert isinstance(restored, Update)
    assert restored.expected_version == 3
    assert restored.new_data.text == "v2"


class Writer(Agent):
    consumes = [Consume(Trigger)]

    def __init__(self, text: str):
        super().__init__(name=text)
        self.text = text

    async def run(self, event, context):
        note = context.get("note:1")
        assert note is not None
        current_effects().update(
            note,
            expected_version=note.version,
            text=self.text,
        )
        return None


def test_parallel_writers_conflict_before_any_commit():
    context = Context()
    context.create(Note(text="base"), id="note:1")
    context.create(Trigger(text="go"), id="trigger:1")
    runtime = Runtime(
        context,
        groups=[Writer("a"), Writer("b")],
        max_concurrency=2,
    )

    with pytest.raises(ArtifactConflict):
        asyncio.run(runtime.arun())

    # Validation happens before the first patch: no partial generation.
    assert context.get("note:1").data.text == "base"  # type: ignore[union-attr]
    assert context.version == 0
