"""The typed Chat facade over ChatAssistant."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel
from reactifact import Chat, step


class Question(BaseModel):
    text: str
    session_id: str = ""


class Answer(BaseModel):
    text: str
    sources: list[str] = []


class TaggedQuestion(BaseModel):
    text: str
    category: str = ""


@step(consumes=Question)
def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper(), sources=["doc.md"])


def test_chat_derives_seed_and_reply_from_types():
    chat = Chat(steps=[answer], seed_type=Question, answer_type=Answer)
    reply = asyncio.run(chat.invoke("hello", session_id="s1"))
    assert reply["reply"] == "HELLO"
    assert reply["sources"] == ["doc.md"]
    assert reply["answer"]["text"] == "HELLO"


def test_chat_seed_factory_reads_request_and_per_turn_wraps_the_run():
    seen: list[tuple[str, str, str]] = []

    @step(consumes=TaggedQuestion)
    def tagged(question: TaggedQuestion) -> Answer:
        return Answer(text=f"{question.category}:{question.text}")

    @asynccontextmanager
    async def per_turn(text: str, session_id: str, request: dict[str, Any]):
        seen.append((text, session_id, request.get("category", "")))
        yield

    chat = Chat(
        steps=[tagged],
        seed_type=TaggedQuestion,
        answer_type=Answer,
        seed_factory=lambda text, sid, req: TaggedQuestion(
            text=text, category=req.get("category", "")
        ),
        per_turn=per_turn,
    )
    reply = asyncio.run(chat.invoke("x", session_id="s2", request={"category": "k8s"}))
    assert reply["reply"] == "k8s:x"
    assert seen == [("x", "s2", "k8s")]


def test_chat_history_delegates_to_the_assistant():
    chat = Chat(steps=[answer], seed_type=Question, answer_type=Answer)
    asyncio.run(chat.invoke("hi", session_id="s3"))
    history = asyncio.run(chat.history("s3"))
    assert history["messages"]
