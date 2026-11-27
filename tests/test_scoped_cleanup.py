"""`ScopedCleanup`: delete a turn's scratch by scope, not by id."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import Agent, Context, Runtime
from reactifact.contracts import describe_contracts
from reactifact.recipes.cleanup import ScopedCleanup


class Question(BaseModel):
    query_id: str
    text: str


class Scratch(BaseModel):
    query_id: str
    text: str


class Final(BaseModel):
    query_id: str
    text: str


def test_scoped_cleanup_deletes_only_the_terminal_turn():
    context = Context()
    context.create(Question(query_id="q1", text="one"))
    context.create(Question(query_id="q2", text="two"))
    context.create(Scratch(query_id="q1", text="scratch q1"))
    context.create(Scratch(query_id="q2", text="scratch q2"))
    context.create(Final(query_id="q1", text="final q1"))

    cleanup = ScopedCleanup(Final, scratch_types=(Scratch,), scope="query_id")
    asyncio.run(Runtime(context, steps=[cleanup]).arun())

    scratches = {a.data.query_id for a in context.list_artifacts(Scratch)}
    assert scratches == {"q2"}


def test_scoped_cleanup_contract_is_honest():
    cleanup = ScopedCleanup(Final, scratch_types=(Scratch,), scope="query_id")
    report = describe_contracts([Agent(steps=[cleanup])]).to_dict()
    step = report["groups"][0]["steps"][0]
    assert step["produces"] == []
    assert step["deletes"] == ["Scratch"]


def test_scoped_cleanup_requires_scopes():
    try:
        ScopedCleanup(Final, scratch_types=(), scope="query_id")
    except ValueError as exc:
        assert "scratch type" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
