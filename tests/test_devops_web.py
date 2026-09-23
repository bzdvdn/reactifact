from examples.devops.web import create_app
from fastapi.testclient import TestClient
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse


class EmptyLLM(LLMProvider):
    """Empty LLM: generates nothing (no network/.env)."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="{}")

    async def stream(self, request):
        yield LLMResponse(text="")


class ScriptedLLM(LLMProvider):
    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request):
        yield LLMResponse(text="")


def test_sse_clarify_asks_and_resumes(tmp_path):
    """HITL: the agent asks for namespace (waiting), the answer resumes the loop."""
    llm = ScriptedLLM(
        [
            '{"target":"k8s"}',
            '{"type":"ask","text":"В каком namespace?"}',
            '{"type":"tool_call","tool":"kubectl_get","args":{"resource":"pods","namespace":"production"}}',
            '{"type":"answer","text":"В production всё ок"}',
        ]
    )
    app = create_app(llm=llm, store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "у меня упал под", "session_id": "s5"},
    ) as response:
        body1 = "".join(response.iter_text())
    assert '"waiting": true' in body1
    assert "В каком namespace?" in body1

    # human answer → resume → the agent continues and gives the final answer
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "production", "session_id": "s5"},
    ) as response:
        body2 = "".join(response.iter_text())
    assert '"waiting": false' in body2
    assert "В production всё ок" in body2


def test_sse_help_for_no_match(tmp_path):
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "привет", "session_id": "s1"},
    ) as response:
        body = "".join(response.iter_text())

    assert "event: session" in body
    assert "event: message" in body
    assert "I help with k8s" in body


def test_sse_k8s_falls_back_honestly(tmp_path):
    """Without an LLM the agent honestly replies "Could not reach a decision" (no silence)."""
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "почему падает под в кластере?", "session_id": "s2"},
    ) as response:
        body = "".join(response.iter_text())

    assert "event: status" in body  # router statuses
    assert "Parsing the question" in body
    assert "event: message" in body
    assert "Could not reach a decision" in body


def test_runs_routes(tmp_path):
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    assert client.get("/api/health").json() == {"ok": True}

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "привет", "session_id": "s3"},
    ):
        pass

    runs = client.get("/api/runs/s3").json()
    assert any(m["role"] == "user" for m in runs["messages"])
    assert any(m["role"] == "assistant" for m in runs["messages"])

    assert client.delete("/api/runs/s3").json() == {"ok": True}
    assert client.get("/api/runs/s3").json()["messages"] == []


def test_traces_ui_and_api(tmp_path):
    """Traces are written and served via /traces and /api/traces."""
    from examples.devops.web import create_app as create_devops_app
    from fastapi.testclient import TestClient as TC

    app = create_devops_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TC(app)

    # empty trace store before requests
    assert client.get("/api/traces").json()["items"] == []

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "привет", "session_id": "s-tr"},
    ):
        pass

    traces = client.get("/api/traces").json()["items"]
    assert len(traces) >= 1
    assert traces[0]["session_id"] == "s-tr"
    assert traces[0]["outcome"] == "completed"

    detail = client.get("/api/traces/" + traces[0]["id"]).json()
    assert "spans" in detail
    assert any(s["agent"] == "route" for s in detail["spans"])

    page = client.get("/traces")
    assert page.status_code == 200
    assert "reactifact" in page.text

    run_page = client.get("/traces/" + traces[0]["id"])
    assert run_page.status_code == 200
    assert "reactifact" in run_page.text
    assert "Evidence graph" in run_page.text
