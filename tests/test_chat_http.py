"""`Chat.http()` — sessions, history and the configurable chat paths."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from reactifact import Chat, step
from reactifact.web import ChatRoutes


class Question(BaseModel):
    text: str
    session_id: str = ""


class Answer(BaseModel):
    text: str


@step(reads=Question)
def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())


def _chat():
    return Chat(steps=[answer], seed_type=Question, answer_type=Answer)


def _routes():
    return ChatRoutes(
        stream="/chat/stream",
        invoke="/chat",
        history="/chat/history",
        clear="/chat/clear",
        clear_method="POST",
        history_messages=True,
    )


def test_chat_invoke_history_and_clear_round_trip():
    client = TestClient(_chat().http(prefix="/api", routes=_routes()))

    reply = client.post("/api/chat", json={"message": "hello", "session_id": "s1"})
    assert reply.status_code == 200
    assert reply.json()["reply"] == "HELLO"

    history = client.get("/api/chat/history", params={"session_id": "s1"})
    assert history.status_code == 200
    assert [message["role"] for message in history.json()] == ["user", "assistant"]
    assert [message["text"] for message in history.json()] == ["hello", "HELLO"]

    cleared = client.post("/api/chat/clear", params={"session_id": "s1"})
    assert cleared.json() == {"ok": True}
    assert client.get("/api/chat/history", params={"session_id": "s1"}).json() == []


def test_chat_stream_still_uses_sessions():
    client = TestClient(_chat().http(prefix="/api", routes=_routes()))

    response = client.post(
        "/api/chat/stream", json={"message": "hi", "session_id": "s2"}
    )

    assert response.status_code == 200
    assert "event: message" in response.text
    history = client.get("/api/chat/history", params={"session_id": "s2"}).json()
    assert len(history) == 2


def test_chat_contracts_include_type_schemas():
    client = TestClient(_chat().http(prefix="/api", routes=_routes()))

    payload = client.get("/api/contracts").json()

    assert payload["seed"]["title"] == "Question"
    assert payload["answer"]["title"] == "Answer"


def test_chat_default_routes_remain_compatible():
    app = FastAPI()
    app.include_router(_chat().router())
    client = TestClient(app)

    assert (
        client.post(
            "/api/chat/stream", json={"message": "hi", "session_id": "s1"}
        ).status_code
        == 200
    )
    assert client.get("/api/runs/s1").status_code == 200
    assert client.delete("/api/runs/s1").json() == {"ok": True}
