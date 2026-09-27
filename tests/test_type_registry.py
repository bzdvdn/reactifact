"""`reactifact.types` — stable artifact type identity (register / TYPE_ID /
aliases / migrate), so a rename or move does not break persisted sessions,
commits, events or recordings."""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel
from reactifact import register_type
from reactifact.artifacts import Artifact
from reactifact.context import Context
from reactifact.events import Event, EventType
from reactifact.operations import Create, operation_from_dict
from reactifact.types import (
    TypeNotFoundError,
    register,
    resolve,
    type_id_of,
)


class Answer(BaseModel):
    text: str


class WithTypeId(BaseModel):
    TYPE_ID: ClassVar[str] = "test.with_type_id"
    text: str


class Custom(BaseModel):
    text: str


class Moved(BaseModel):
    text: str


class Renamed(BaseModel):
    prose: str


class Dup1(BaseModel):
    text: str


class Dup2(BaseModel):
    text: str


register(Custom, type_id="test.custom")
register(Moved, type_id="test.moved", aliases=["legacy.module.Moved"])
register(
    Renamed, type_id="test.renamed", migrate=lambda d: {**d, "prose": d.pop("text")}
)


def test_default_type_id_is_the_qualified_name():
    assert type_id_of(Answer) == f"{Answer.__module__}.{Answer.__qualname__}"


def test_type_id_classvar_overrides_the_qualified_name():
    assert type_id_of(WithTypeId) == "test.with_type_id"


def test_register_exports_from_the_package():
    assert register_type is register


def test_registered_type_id_is_written_and_loads_back():
    artifact = Artifact(data=Custom(text="hi"))

    assert artifact.data_type == "test.custom"
    assert artifact.to_dict()["data_type"] == "test.custom"
    restored = Artifact.from_dict(artifact.to_dict())
    assert isinstance(restored.data, Custom)
    assert restored.data.text == "hi"


def test_alias_resolves_a_previously_persisted_id():
    assert resolve("legacy.module.Moved") is Moved
    assert resolve("test.moved") is Moved

    restored = Artifact.from_dict(
        {
            "id": "a1",
            "data_type": "legacy.module.Moved",
            "data": {"text": "moved"},
            "version": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "history": [],
            "created_by_commit": None,
        }
    )
    assert isinstance(restored.data, Moved)
    assert restored.data.text == "moved"


def test_migrate_hook_runs_before_validation_on_load():
    restored = Artifact.from_dict(
        {
            "id": "a2",
            "data_type": "test.renamed",
            "data": {"text": "renamed"},
            "version": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "history": [],
            "created_by_commit": None,
        }
    )
    assert isinstance(restored.data, Renamed)
    assert restored.data.prose == "renamed"


def test_migration_is_applied_to_commit_operations_too():
    op = operation_from_dict(
        {
            "type": "create",
            "data": {"text": "renamed"},
            "data_type": "test.renamed",
            "artifact_id": None,
            "id": None,
        }
    )
    assert isinstance(op, Create)
    assert isinstance(op.data, Renamed)
    assert op.data.prose == "renamed"


def test_event_type_uses_the_stable_id_and_resolves_back():
    event = Event(EventType.ARTIFACT_CREATED, Custom, "id-1")

    data = event.to_dict()
    assert data["artifact_type"] == "test.custom"
    assert Event.from_dict(data).artifact_type is Custom


def test_event_with_an_unknown_type_falls_back_to_the_string():
    event = Event.from_dict(
        {
            "type": "artifact_created",
            "artifact_type": "not.importable.Type",
            "artifact_id": "x",
        }
    )
    assert event.artifact_type == "not.importable.Type"


def test_unregistered_model_still_loads_by_qualified_name():
    artifact = Artifact(data=Answer(text="x"))

    assert artifact.data_type == f"{Answer.__module__}.{Answer.__qualname__}"
    assert isinstance(Artifact.from_dict(artifact.to_dict()).data, Answer)


def test_session_roundtrip_with_a_registered_type_id():
    ctx = Context()
    ctx.create(Custom(text="stored"))

    restored = Context.from_dict(ctx.to_dict())

    assert [a.data for a in restored.list_artifacts(Custom)] == [Custom(text="stored")]


def test_two_models_cannot_share_a_type_id():
    register(Dup1, type_id="test.dup")

    with pytest.raises(ValueError, match="already registered"):
        register(Dup2, type_id="test.dup")


def test_unknown_type_id_raises_a_clear_error():
    with pytest.raises(TypeNotFoundError, match="unknown artifact type"):
        resolve("nope.Nope")
