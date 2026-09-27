"""chat — a minimal single-model multi-turn conversation, fully offline.

Where the `repair`/`devops`/`knowledge` examples have *separate* question and
answer artifact types (and use `Transcript` to merge them), this one shows the
other shape: one message model, `ConversationMessage`, that both sides write —
`recipes.conversation.Conversation` owns appending turns and rendering the
thread, and `ChatMemory(keep=…)` bounds it.

A deterministic echo agent answers, so there is no LLM and no API key.

Run:  .venv/bin/python -m examples.chat.main
"""

from __future__ import annotations

import asyncio
from typing import Any

from reactifact import Agent, Consume, Produce, ProduceCall, create_agent
from reactifact.chat import ChatAssistant, ChatMemory
from reactifact.checkpoints import InMemoryKVBackend
from reactifact.context import Context
from reactifact.recipes.conversation import Conversation, ConversationMessage
from reactifact.session import SessionStore

KEEP = 4  # ChatMemory keeps the last N messages
SESSION_ID = "demo"


class Echo(Produce[ConversationMessage]):
    """A deterministic stand-in for a real assistant (no LLM)."""

    artifact_type = ConversationMessage

    async def produce(self, call: ProduceCall) -> None:
        trigger = call.trigger
        if trigger is None or trigger.data.role != "user":
            return None
        call.effects.create(
            ConversationMessage(
                role="assistant",
                text=f"[echo] {trigger.data.text}",
                turn=trigger.data.turn,
            )
        )


def _agent() -> Agent:
    return create_agent(
        "echo", consumes=[Consume(ConversationMessage)], produces=[Echo()]
    )


def _reply(ctx: Context, msg_id: str) -> dict[str, Any]:
    """The assistant side of this turn: the latest assistant message."""
    texts = [
        a.data.text
        for a in ctx.list_artifacts(ConversationMessage)
        if a.data.role == "assistant"
    ]
    return {"reply": texts[-1] if texts else "(no reply)"}


def build() -> tuple[ChatAssistant, Conversation, SessionStore]:
    """The assistant, its conversation model, and the in-memory store."""
    conv = Conversation()  # one message model for both sides
    store = SessionStore(InMemoryKVBackend())
    assistant = ChatAssistant(
        store=store,
        agents=[_agent()],
        user_message=ConversationMessage,
        create_message=conv.create_turn,  # record each user turn
        reply=_reply,
        session_state=conv.state,  # history() shape
        memory=ChatMemory(message_type=ConversationMessage, keep=KEEP),
    )
    return assistant, conv, store


TURNS = [
    "what's the refund policy?",
    "and the pricing?",
    "where is my order?",
    "thanks!",
]


async def run() -> dict[str, Any]:
    """Run the turns and return the (bounded) thread — used by the test."""
    assistant, conv, store = build()
    for text in TURNS:
        await assistant.invoke(text, session_id=SESSION_ID)
    session = await store.open(SESSION_ID)
    return {
        "kept": len(session.context.list_artifacts(ConversationMessage)),
        "transcript": [
            (m.role, m.content) for m in conv.transcript(session.context, window=KEEP)
        ],
    }


async def main() -> None:
    assistant, conv, store = build()
    for text in TURNS:
        print("You:", text)
        print("AI: ", (await assistant.invoke(text, session_id=SESSION_ID))["reply"])

    session = await store.open(SESSION_ID)
    thread = session.context.list_artifacts(ConversationMessage)
    print(f"\nthread kept: {len(thread)} (ChatMemory keep={KEEP})")
    print("transcript (the window that survives):")
    for message in conv.transcript(session.context, window=KEEP):
        print(f"  {message.role:>9}: {message.content}")


if __name__ == "__main__":
    asyncio.run(main())
