from __future__ import annotations

import pytest
from pydantic import BaseModel
from reactifact import Context, EventType, Trigger
from reactifact.events import Event


class Doc(BaseModel):
    text: str


class SubDoc(Doc):
    pass


def _event(
    event_type: EventType,
    artifact_type: type,
    artifact_id: str,
) -> Event:
    return Event(event_type, artifact_type, artifact_id)


def test_trigger_matches_event_and_exact_type() -> None:
    context = Context()
    doc = context.create(Doc(text="x"))
    event = _event(EventType.ARTIFACT_CREATED, Doc, doc.id)

    assert Trigger(EventType.ARTIFACT_CREATED, Doc).matches(event)
    assert not Trigger(EventType.ARTIFACT_UPDATED, Doc).matches(event)
    assert not Trigger(EventType.ARTIFACT_CREATED, SubDoc).matches(event)
    assert Trigger(EventType.ARTIFACT_CREATED).matches(event)


def test_trigger_include_subclasses() -> None:
    context = Context()
    doc = context.create(SubDoc(text="x"))
    event = _event(EventType.ARTIFACT_CREATED, SubDoc, doc.id)

    assert Trigger(EventType.ARTIFACT_CREATED, Doc, include_subclasses=True).matches(
        event
    )
    assert not Trigger(EventType.ARTIFACT_CREATED, Doc).matches(event)


def test_condition_requires_context() -> None:
    context = Context()
    doc = context.create(Doc(text="x"))
    event = _event(EventType.ARTIFACT_CREATED, Doc, doc.id)
    trigger = Trigger(
        EventType.ARTIFACT_CREATED,
        Doc,
        condition=lambda artifact: artifact.data.text == "x",
    )

    assert trigger.matches(event, context)
    assert not trigger.matches(
        _event(EventType.ARTIFACT_CREATED, Doc, "missing"), context
    )
    with pytest.raises(ValueError, match="Context is required"):
        trigger.matches(event)


def test_context_condition_and_debounce_do_not_change_matching() -> None:
    context = Context()
    doc = context.create(Doc(text="x"))
    event = _event(EventType.ARTIFACT_CREATED, Doc, doc.id)
    trigger = Trigger(
        EventType.ARTIFACT_CREATED,
        Doc,
        context_condition=lambda artifact, ctx: bool(ctx.list_artifacts(Doc)),
        debounce=True,
    )

    assert trigger.matches(event, context)
    assert trigger.debounce is True
    # `debounce` is a Runtime hint; `matches()` is unaffected by it.
    assert Trigger(EventType.ARTIFACT_CREATED, Doc).matches(event)
