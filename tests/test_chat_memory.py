"""`ChatAssistant(memory=ChatMemory(...))` — bounded multi-turn memory wired in
automatically: prune the raw thread and/or compact the commit history."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact.agents import create_agent
from reactifact.chat import ChatAssistant, ChatMemory
from reactifact.checkpoints import InMemoryKVBackend
from reactifact.consume import Consume
from reactifact.context import Context
from reactifact.produce import Produce, ProduceCall
from reactifact.recipes.conversation import (
    ConversationMessage,
    create_turn,
)
from reactifact.session import SessionStore


class _Echo(Produce[ConversationMessage]):
    artifact_type = ConversationMessage

    async def produce(self, call: ProduceCall) -> None:
        trigger = call.trigger
        if trigger is not None and trigger.data.role == "user":
            call.effects.create(
                ConversationMessage(
                    role="assistant",
                    text=f"echo: {trigger.data.text}",
                    turn=trigger.data.turn,
                )
            )


def _echo_agent():
    return create_agent(
        "echo",
        consumes=[Consume(ConversationMessage)],
        produces=[_Echo()],
    )


def _reply(ctx: Context, msg_id: str) -> dict[str, str]:
    texts = [
        a.data.text
        for a in ctx.list_artifacts(ConversationMessage)
        if a.data.role == "assistant"
    ]
    return {"reply": texts[-1] if texts else ""}


def _assistant(**memory_kwargs) -> tuple[ChatAssistant, SessionStore]:
    store = SessionStore(InMemoryKVBackend())
    memory = (
        ChatMemory(message_type=ConversationMessage, **memory_kwargs)
        if memory_kwargs
        else None
    )
    assistant = ChatAssistant(
        store=store,
        agents=[_echo_agent()],
        user_message=ConversationMessage,
        reply=_reply,
        create_message=create_turn,
        memory=memory,
    )
    return assistant, store


def test_chat_memory_prunes_the_raw_thread():
    assistant, store = _assistant(keep=2)

    for i in range(5):
        assert asyncio.run(assistant.invoke(f"m{i}", session_id="s"))["reply"] == (
            f"echo: m{i}"
        )

    ctx = asyncio.run(store.open("s")).context
    assert len(ctx.list_artifacts(ConversationMessage)) <= 2


def test_chat_memory_compacts_the_commit_history():
    assistant, store = _assistant(compact_commits=2)

    for i in range(5):
        asyncio.run(assistant.invoke(f"m{i}", session_id="s"))

    ctx = asyncio.run(store.open("s")).context
    assert ctx.compacted_at > 0


def test_chat_memory_compaction_only_adds_no_message_agent():
    assistant, _ = _assistant(compact_commits=1)

    assert assistant._memory_agents() == []


def test_chat_memory_keep_requires_a_message_type():
    assistant = ChatAssistant(
        store=SessionStore(InMemoryKVBackend()),
        agents=[],
        user_message=ConversationMessage,
        reply=_reply,
        memory=ChatMemory(keep=5),  # no message_type
    )

    with pytest.raises(ValueError, match="message_type"):
        assistant._memory_agents()


def test_chat_memory_summarize_requires_a_summary_type():
    assistant, _ = _assistant(summarize=lambda ctx, previous, stale: None)

    with pytest.raises(ValueError, match="summary_type"):
        assistant._memory_agents()


def test_chat_memory_digest_defaults_to_a_text_summary_model():
    class Summary(BaseModel):
        text: str

    assistant, _ = _assistant(
        summarize=lambda ctx, previous, stale: None, summary_type=Summary
    )

    agents = assistant._memory_agents()
    assert len(agents) == 1


def test_chat_memory_digest_requires_build_without_a_text_field():
    class Summary(BaseModel):
        note: str

    assistant, _ = _assistant(
        summarize=lambda ctx, previous, stale: None, summary_type=Summary
    )

    with pytest.raises(ValueError, match="build_summary"):
        assistant._memory_agents()
