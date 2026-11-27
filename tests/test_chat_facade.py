"""The typed Chat facade over ChatAssistant."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import BaseModel
from reactifact import Chat, RuntimeResources, step


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


def test_chat_test_client_isolates_sessions_and_traces(tmp_path):
    chat = Chat(
        steps=[answer],
        seed_type=Question,
        answer_type=Answer,
        store=str(tmp_path / "prod"),
    )
    with chat.test_client() as client:
        reply = asyncio.run(client.invoke("hi", session_id="s1"))
        assert reply["reply"] == "HI"
        assert asyncio.run(client.history("s1"))["messages"]
        assert asyncio.run(chat.history("s1"))["messages"] == []
        assert client.traces is not None
        assert asyncio.run(client.traces.query(limit=10))["items"]


def test_chat_router_builds_a_fastapi_router():
    chat = Chat(steps=[answer], seed_type=Question, answer_type=Answer)
    router = chat.router(prefix="/chat")

    paths = {route.path for route in router.routes}
    assert "/chat/chat/stream" in paths
    assert "/chat/runs/{session_id}" in paths
    assert "/chat/health" in paths


class Svc:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix


@step(consumes=Question)
def via_service(question: Question, svc: Svc) -> Answer:
    return Answer(text=f"{svc.prefix}:{question.text}")


def test_chat_answer_returns_the_typed_model():
    chat = Chat(steps=[answer], seed_type=Question, answer_type=Answer)
    result = asyncio.run(chat.answer("hello", session_id="typed"))
    assert isinstance(result, Answer)
    assert result.text == "HELLO"


def test_chat_answer_falls_back_to_the_session_for_a_custom_reply():
    chat = Chat(
        steps=[answer],
        seed_type=Question,
        answer_type=Answer,
        reply=lambda ctx, msg_id: {"reply": "custom", "waiting": False},
    )
    result = asyncio.run(chat.answer("hello", session_id="custom-reply"))
    assert isinstance(result, Answer)
    assert result.text == "HELLO"


def test_chat_traces_shortcut_records_runs():
    chat = Chat(
        steps=[answer],
        seed_type=Question,
        answer_type=Answer,
        traces=True,
    )
    try:
        asyncio.run(chat.invoke("hello", session_id="traced"))
        assert chat.trace_store is not None
        assert asyncio.run(chat.trace_store.query(limit=10))["items"]
    finally:
        chat.close()


def test_chat_lifespan_supplies_resources_once():
    @asynccontextmanager
    async def lifespan():
        resources = RuntimeResources()
        resources.register(Svc, Svc("live"))
        yield resources

    chat = Chat(
        steps=[via_service],
        seed_type=Question,
        answer_type=Answer,
        lifespan=lifespan,
    )
    with pytest.raises(RuntimeError, match="lifespan"):
        asyncio.run(chat.invoke("outside", session_id="no-enter"))

    async def run():
        async with chat:
            return await chat.answer("inside", session_id="entered")

    assert asyncio.run(run()).text == "live:inside"


def test_test_chat_overrides_a_typed_resource():
    chat = Chat(
        steps=[via_service],
        seed_type=Question,
        answer_type=Answer,
        resources=RuntimeResources(),
    )
    with chat.test_client() as client:
        client.override(Svc, Svc("fake"))
        assert asyncio.run(client.answer("x", session_id="override")).text == "fake:x"


class TurnAnswer(BaseModel):
    query_id: str
    text: str


@step(consumes=Question, produces=TurnAnswer)
def turn_answer(question: Question, call) -> TurnAnswer:
    return TurnAnswer(query_id=call.trigger.id, text=question.text.upper())


def test_chat_accepts_a_declarative_chat_reply():
    from reactifact.chat import ChatReply

    reply = ChatReply(
        TurnAnswer,
        render=lambda data: data.text.lower(),
        extra=lambda artifact: {"answer_id": artifact.id},
    )
    chat = Chat(
        steps=[turn_answer],
        seed_type=Question,
        answer_type=TurnAnswer,
        reply=reply,
    )
    payload = asyncio.run(chat.invoke("hello", session_id="s-reply"))
    assert payload["reply"] == "hello"
    assert payload["answer_id"]
