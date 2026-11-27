import json

from examples.knowledge.web import create_app
from fastapi.testclient import TestClient
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse


class EmptyLLM(LLMProvider):
    """Honestly empty LLM: generates nothing (independent of .env/network)."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="{}")

    async def stream(self, request):
        yield LLMResponse(text="")


class BoomLLM(LLMProvider):
    """A provider that fails: network/5xx."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise RuntimeError("upstream down")

    async def stream(self, request):
        yield LLMResponse(text="")


def test_sse_greeting(tmp_path):
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hello", "session_id": "s1"},
    ) as response:
        body = "".join(response.iter_text())

    assert "event: session" in body
    assert "event: message" in body
    assert "Hello!" in body


def test_sse_research_answer_with_sources(tmp_path):
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "how much does GPU inference cost?", "session_id": "s2"},
    ) as response:
        body = "".join(response.iter_text())

    assert "event: status" in body  # live progress "Searching…", "Found…"
    assert "Searching for information" in body
    assert "knowledge:pricing/tiers.md" in body  # answer sources
    assert "event: message" in body


def test_runs_routes(tmp_path):
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    assert client.get("/api/health").json() == {"ok": True}

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hello", "session_id": "s3"},
    ):
        pass

    runs = client.get("/api/runs/s3").json()
    assert any(m["role"] == "user" for m in runs["messages"])
    assert any(m["role"] == "assistant" for m in runs["messages"])

    assert client.delete("/api/runs/s3").json() == {"ok": True}
    assert client.get("/api/runs/s3").json()["messages"] == []


def test_llm_failure_not_empty_reply(tmp_path):
    app = create_app(llm=BoomLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "how to set up authentication?", "session_id": "s4"},
    ) as response:
        body = "".join(response.iter_text())

    # not an empty reply and no cut-off: honest fallback (§59)
    assert "event: message" in body
    assert '"reply": ""' not in body


def test_sse_calculation_and_claims_payload(tmp_path):
    """Agent 5/6: the answer to a calculation question carries claims + calculations."""
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={
            "message": "how much does GPU inference cost in total?",
            "session_id": "s5",
        },
    ) as response:
        body = "".join(response.iter_text())

    msg_events = [
        part.split("\n", 1)[1]
        for part in body.split("\n\n")
        if part.startswith("event: message")
    ]
    assert msg_events, "должен прийти терминальный message"
    payload = json.loads(msg_events[-1].removeprefix("data: "))
    assert any("Sum over column" in c["description"] for c in payload["calculations"])
    assert payload["claims"], "верификатор должен построить утверждения"
    assert all(0 <= c["confidence"] <= 1 for c in payload["claims"])
    assert any("costs:gpu_usage.csv" in s for s in payload["sources"])


def test_trace_dashboard_and_knowledge_status(tmp_path):
    """The migrated demo records agent traces and exposes ingestion status."""
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "how to set up authentication?", "session_id": "s6"},
    ) as response:
        body = "".join(response.iter_text())
    assert "knowledge:guide/auth.md" in body

    assert client.get("/traces").status_code == 200
    traces = client.get("/api/traces").json()
    assert traces["total"] >= 1

    assert client.get("/knowledge/").status_code == 200
    status = client.get("/knowledge/api/knowledge/status").json()
    assert status["sources"][0]["source_id"] == "knowledge"
    assert status["index"]["type"] == "SqliteIndex"
    assert status["index"]["count"] > 0


def test_contracts_page_is_mounted(tmp_path):
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    page = client.get("/contracts")
    assert page.status_code == 200
    assert "knowledge-ai contracts" in page.text

    payload = client.get("/api/contracts").json()
    assert payload["groups"]
