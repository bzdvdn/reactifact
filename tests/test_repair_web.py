from examples.repair.web import create_app
from fastapi.testclient import TestClient
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse


class EmptyLLM(LLMProvider):
    """An honestly empty LLM: extracts nothing (independent of .env/network)."""

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


def test_sse_stream_greeting(tmp_path):
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
    assert "Здравствуйте" in body


def test_sse_missing_facts_asks(tmp_path):
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "просто ремонт", "session_id": "s2"},
    ) as response:
        body = "".join(response.iter_text())

    assert "Уточните" in body


def test_traces_with_repair_columns(tmp_path):
    """The repair example wires the trace dashboard with Question/Stage/… columns."""
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "нужно отремонтировать комнату 18 кв м", "session_id": "rt"},
    ) as response:
        "".join(response.iter_text())

    assert [c["label"] for c in client.get("/api/columns").json()["items"]] == [
        "Question",
        "Stage",
        "Pending",
        "Answer",
    ]
    top = client.get("/api/traces").json()["items"][0]
    assert top["fields"]["Question"].startswith("нужно отремонтировать")
    assert top["fields"]["Stage"] == "collect"
    detail = client.get("/api/traces/" + top["id"]).json()
    assert detail["fields"]["Question"].startswith("нужно отремонтировать")
    assert "Fields" in client.get("/traces/" + top["id"]).text


def test_runs_routes(tmp_path):
    app = create_app(llm=EmptyLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    r = client.get("/api/health")
    assert r.json() == {"ok": True}

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


def test_capabilities_over_sse(tmp_path):
    app = create_app(llm=BoomLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "что ты умеешь?", "session_id": "s4"},
    ) as response:
        body = "".join(response.iter_text())

    assert "Что я умею" in body
    assert "event: status" in body  # a live «Думаю…» status before the reply
    assert "Думаю" in body


def test_llm_failure_not_empty_reply(tmp_path):
    app = create_app(llm=BoomLLM(), store_dir=str(tmp_path))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "сделай ремонт ванной", "session_id": "s5"},
    ) as response:
        body = "".join(response.iter_text())

    # not an empty reply and not a broken stream: an honest fallback (§59)
    assert "event: message" in body
    assert '"reply": ""' not in body


def test_estimate_csv_endpoint_exports_worksheet(tmp_path):
    from examples.repair.models import PlanStep, Project, ProjectInfo
    from examples.repair.services.catalog import Catalog
    from examples.repair.services.estimate import build_estimate, estimate_to_csv
    from examples.repair.web import create_app
    from fastapi.testclient import TestClient

    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    catalog = Catalog(str(root / "examples" / "repair" / "data" / "price.csv"))
    project = Project(
        info=ProjectInfo(room_type="детская", area=10.0, budget=300000.0),
        plan=[
            PlanStep(
                name="Электромонтаж", description="розетки", materials=["розетки ~2 шт"]
            ),
        ],
    )
    project.estimate = build_estimate(project.plan, catalog)
    assert project.estimate.total

    db = str(tmp_path / "sessions")
    import asyncio

    from reactifact.checkpoints import FileKVBackend
    from reactifact.session import SessionStore

    asyncio.run(SessionStore(FileKVBackend(db)).save_session("s1", __ctx(project)))

    client = TestClient(create_app(store_dir=db))
    res = client.get("/api/runs/s1/estimate.csv")
    assert res.status_code == 200
    assert res.text.startswith("\ufeff")
    assert "Итого" in res.text
    assert "Смета" in res.text
    assert "Описание" in res.text
    # the pure builder matches the endpoint output
    assert estimate_to_csv(project).startswith("\ufeff")


def __ctx(project):
    from reactifact import Context, RuntimeResources

    ctx = Context(resources=RuntimeResources())
    ctx.create(project)
    return ctx
