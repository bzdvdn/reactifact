"""`recipes.conversation` — the multi-turn conversation recipe: the default
`ConversationMessage`, the generic `Conversation` over a custom model, and its
interaction with the memory recipes."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact.agents import create_agent
from reactifact.consume import Consume
from reactifact.context import Context
from reactifact.recipes.conversation import (
    Conversation,
    ConversationMessage,
    MessageSpec,
    Transcript,
    add_message,
    conversation_state,
    create_turn,
    transcript,
    turn_count,
)
from reactifact.recipes.memory import WindowPruner
from reactifact.runtime import Runtime


class MyMsg(BaseModel):
    """A custom message model: `role`/`text` is all the recipe needs."""

    role: str
    text: str
    turn: int = 0


class Q(BaseModel):
    text: str


class A(BaseModel):
    text: str


def test_transcript_turn_count_and_window():
    ctx = Context()
    add_message(ctx, "system", "be brief")
    add_message(ctx, "user", "hi")
    add_message(ctx, "assistant", "hello")
    add_message(ctx, "user", "again")

    assert turn_count(ctx) == 2
    assert [(m.role, m.content) for m in transcript(ctx)] == [
        ("system", "be brief"),
        ("user", "hi"),
        ("assistant", "hello"),
        ("user", "again"),
    ]
    assert [m.content for m in transcript(ctx, window=2)] == ["hello", "again"]
    assert all(m.role != "system" for m in transcript(ctx, include_system=False))


def test_create_turn_returns_id_and_numbers_turns():
    ctx = Context()

    first = ctx.get(create_turn(ctx, "q1"))
    assert first is not None
    assert first.data.role == "user" and first.data.turn == 1

    add_message(ctx, "assistant", "a1")
    second = ctx.get(create_turn(ctx, "q2"))
    assert second is not None
    assert second.data.turn == 2


def test_add_message_with_stable_id_is_idempotent():
    ctx = Context()
    a = add_message(ctx, "user", "hi", id="turn:1:user")
    b = add_message(ctx, "user", "hi", id="turn:1:user")

    assert a.id == b.id
    assert len(ctx.list_artifacts(ConversationMessage)) == 1


def test_conversation_is_generic_over_a_custom_model():
    ctx = Context()
    conv = Conversation(MyMsg)

    conv.create_turn(ctx, "hi")
    conv.add(ctx, "assistant", "yo")

    assert conv.turns(ctx) == 1
    assert [(m.role, m.content) for m in conv.transcript(ctx)] == [
        ("user", "hi"),
        ("assistant", "yo"),
    ]
    assert len(ctx.list_artifacts(MyMsg)) == 2
    # the module-level helpers are bound to ConversationMessage, so they see none
    assert turn_count(ctx) == 0
    assert transcript(ctx) == []


def test_conversation_state_payload():
    ctx = Context()
    add_message(ctx, "user", "hi")

    payload = conversation_state(ctx)

    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][0]["turn"] == 1


def test_transcript_merges_several_typed_turns_by_role():
    ctx = Context()
    ctx.create(Q(text="refund?"))
    ctx.create(A(text="14 days"))
    ctx.create(Q(text="and pricing?"))

    view = Transcript(
        [
            MessageSpec(Q, "user"),
            MessageSpec(A, "assistant", render=lambda d: f"[{d.text}]"),
        ]
    )

    assert [(m.role, m.content) for m in view.messages(ctx)] == [
        ("user", "refund?"),
        ("assistant", "[14 days]"),
        ("user", "and pricing?"),
    ]
    assert [m.content for m in view.messages(ctx, window=1)] == ["and pricing?"]
    assert view.state(ctx)["messages"][-1]["text"] == "and pricing?"


class _Echo:
    pass


def test_memory_recipes_bound_a_conversation():
    """`role`/`text` is enough for `WindowPruner` to prune the thread."""
    ctx = Context()
    memory = create_agent(
        "memory",
        consumes=[Consume(ConversationMessage)],
        produces=[WindowPruner(ConversationMessage, keep=2)],
    )
    runtime = Runtime(ctx, agents=[memory])

    async def build():
        for i in range(5):
            add_message(ctx, "user", f"m{i}")
            await runtime.arun()

    asyncio.run(build())
    assert len(ctx.list_artifacts(ConversationMessage)) <= 2
