"""The starter chat agent: one typed question -> one reply artifact."""

from __future__ import annotations

from reactifact import Artifact, Consume, LLMProvider
from reactifact.agent import agent
from reactifact.quick import Question
from reactifact.structured import llm_reply

from ..models import ChatReply

#: Shown when no provider is configured or a call fails — never a fake answer.
OFFLINE_REPLY = (
    "No model configured. Set OPENROUTER_API_KEY, or OPENAI_BASE_URL + "
    "OPENAI_API_KEY, in examples/starter_app/.env — or use the offline "
    "'rag' mode, which still cites your documents."
)


@agent(name="chat", consumes=[Consume(Question)], produces=ChatReply)
async def chat_reply(
    question: Artifact[Question],
    llm: LLMProvider | None,
) -> ChatReply:
    text = await llm_reply(
        llm,
        system="You are a concise, friendly assistant.",
        user=question.data.text,
    )
    return ChatReply(text=text or OFFLINE_REPLY)


__all__ = ["OFFLINE_REPLY", "chat_reply"]
