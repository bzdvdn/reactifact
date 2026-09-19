"""The starter app: the four quick cases over HTTP, offline-deterministic."""

from collections.abc import AsyncIterator

from examples.starter_app.app import create_app
from fastapi.testclient import TestClient
from reactifact.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
)


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[str]):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        yield LLMResponseChunk(text="")


def _client(tmp_path, llm: LLMProvider) -> TestClient:
    return TestClient(create_app(store_dir=tmp_path, llm=llm))


def test_provider_and_health(tmp_path):
    with _client(tmp_path, ScriptedLLM([])) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/api/provider").json()["mode"] == "model"


def test_ask_agent_mode_returns_structured_text(tmp_path):
    with _client(tmp_path, ScriptedLLM(['{"text":"Hello"}'])) as client:
        res = client.post("/api/ask", json={"mode": "agent", "question": "say hi"})
        assert res.status_code == 200
        assert res.json() == {"mode": "agent", "text": "Hello", "sources": []}


def test_ask_rag_mode_cites_the_document(tmp_path):
    with _client(tmp_path, ScriptedLLM(['{"text":"Within 14 days."}'])) as client:
        res = client.post(
            "/api/ask",
            json={"mode": "rag", "question": "how do refunds work?"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["mode"] == "rag"
        assert body["text"] == "Within 14 days."
        assert "refunds.md" in body["sources"]


def test_ask_rag_mode_offline_returns_passages_with_citations(tmp_path):
    # no usable model response -> the app answers with the matched passages
    with _client(tmp_path, ScriptedLLM([])) as client:
        res = client.post(
            "/api/ask",
            json={"mode": "rag", "question": "how do refunds work?"},
        )
        body = res.json()
        assert "14 days" in body["text"]
        assert "refunds.md" in body["sources"]


def test_ask_tools_mode_returns_answer(tmp_path):
    with _client(tmp_path, ScriptedLLM(['{"type":"answer","text":"3"}'])) as client:
        res = client.post(
            "/api/ask", json={"mode": "tools", "question": "what is 1 + 2?"}
        )
        assert res.status_code == 200
        assert res.json()["text"] == "3"


def test_chat_mode_persists_a_session(tmp_path):
    with _client(tmp_path, ScriptedLLM(['{"text":"Hi there"}'])) as client:
        res = client.post("/api/chat", json={"message": "hello", "session_id": "s1"})
        assert res.status_code == 200
        assert res.json() == {"session_id": "s1", "reply": "Hi there"}

        history = client.get("/api/runs/s1")
        assert history.status_code == 200
        texts = [m["text"] for m in history.json()["messages"]]
        assert "hello" in texts and "Hi there" in texts
