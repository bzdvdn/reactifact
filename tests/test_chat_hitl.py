"""HITL as the next chat turn: waiting payload, history and resume."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import Chat, Consume, Human, PendingQuestion, step


class Query(BaseModel):
    text: str
    session_id: str = ""


class Reply(BaseModel):
    text: str


@step(consumes=Query, produces=PendingQuestion)
def ask(query: Query, human: Human) -> None:
    human.ask("Deploy to production?", kind="approval")


@step(
    consumes=Consume.by_field(PendingQuestion, "answered", True),
    produces=Reply,
)
def finish(question: PendingQuestion) -> Reply:
    return Reply(text=f"answer: {question.answer_text()}")


def _chat(*, resume_pending: bool = True) -> Chat:
    return Chat(
        steps=[ask, finish],
        seed_type=Query,
        answer_type=Reply,
        resume_pending=resume_pending,
    )


def _run(coro):
    return asyncio.run(coro)


def test_chat_waits_by_default_and_the_next_turn_resumes():
    chat = _chat()

    first = _run(chat.invoke("ship it", session_id="s1"))
    assert first["waiting"] is True
    assert first["reply"] == "Deploy to production?"
    assert first["question"]["kind"] == "approval"
    assert first["question"]["id"]

    second = _run(chat.invoke("yes", session_id="s1"))
    assert second["waiting"] is False
    assert second["reply"] == "answer: yes"
    assert second["answer"]["text"] == "answer: yes"


def test_chat_history_exposes_the_pending_question():
    chat = _chat()
    _run(chat.invoke("ship it", session_id="s1"))

    history = _run(chat.history("s1"))["messages"]

    assert any(
        message.get("waiting") is True and message["text"] == "Deploy to production?"
        for message in history
    )


def test_resume_pending_is_opt_in():
    chat = _chat(resume_pending=False)

    first = _run(chat.invoke("ship it", session_id="s1"))
    second = _run(chat.invoke("yes", session_id="s1"))

    assert first["waiting"] is True
    assert second["waiting"] is True
    assert second["question"]["id"] != first["question"]["id"]
