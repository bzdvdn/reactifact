"""Conversation digest/memory helpers added in 0.25."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import Context, FakeLLM, RuntimeResources
from reactifact.chat import ChatMemory
from reactifact.prompts import PromptTemplate
from reactifact.recipes.conversation import MessageSpec, Transcript
from reactifact.recipes.memory import llm_digest_summarizer


class Message(BaseModel):
    text: str


class Question(Message):
    pass


class Answer(Message):
    pass


class DigestBody(BaseModel):
    text: str


def _transcript() -> Transcript:
    return Transcript(
        [
            MessageSpec(Question, "user"),
            MessageSpec(Answer, "assistant"),
        ]
    )


def test_digest_summarizer_uses_transcript_and_structured_schema():
    context = Context(
        resources=RuntimeResources(llm=FakeLLM('{"text": "folded summary"}'))
    )
    stale = [
        context.create(Question(text="old question")),
        context.create(Answer(text="old answer")),
    ]
    summarize = llm_digest_summarizer(
        "fold the conversation",
        user=PromptTemplate("previous: {previous}\nnew: {history}"),
        render=_transcript().render,
        schema=DigestBody,
    )
    result = asyncio.run(summarize(context, "old digest", stale))
    assert result == "folded summary"


def test_digest_summarizer_falls_back_without_llm_and_truncates():
    context = Context()
    stale = [context.create(Question(text="question " * 100))]
    summarize = llm_digest_summarizer(
        "fold",
        render=_transcript().render,
        max_fallback_chars=40,
    )
    result = asyncio.run(summarize(context, "previous", stale))
    assert result is not None
    assert len(result) <= 40
    assert "question" in result


def test_digest_summarizer_falls_back_on_blank_or_error():
    context = Context(resources=RuntimeResources(llm=FakeLLM('{"text": ""}')))
    stale = [context.create(Question(text="q"))]
    summarize = llm_digest_summarizer(
        "fold",
        render=_transcript().render,
        schema=DigestBody,
        fallback=lambda previous, history: f"{previous}|{history}",
    )
    result = asyncio.run(summarize(context, "prev", stale))
    assert result == "prev|user: q"


def test_chat_memory_from_transcript_uses_every_message_type():
    memory = ChatMemory.from_transcript(_transcript(), keep=4)
    assert memory.resolved_message_types() == (Question, Answer)
    assert memory.keep == 4
