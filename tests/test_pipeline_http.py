"""`Pipeline.describe()` / `.validate()` and the FastAPI HTTP adapter."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from reactifact import Pipeline, step
from reactifact.web import PipelineRoutes


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(consumes=Question)
def answer(question: Question) -> Answer:
    return Answer(text=question.text.upper())


def _pipeline(**kwargs):
    return Pipeline(
        steps=[answer],
        seed_type=Question,
        answer_type=Answer,
        **kwargs,
    )


def test_describe_includes_contracts_and_type_schemas():
    pipeline = _pipeline()

    report = pipeline.describe()
    payload = report.to_dict()

    assert payload["groups"][0]["name"] == "answer"
    assert payload["seed"]["title"] == "Question"
    assert payload["answer"]["title"] == "Answer"
    assert pipeline.validate().ok


def test_http_run_and_openapi_are_generated_from_the_types():
    client = TestClient(_pipeline().http())

    response = client.post("/run", json={"text": "hello"})

    assert response.status_code == 200
    assert response.json() == {"text": "HELLO"}
    assert response.headers["x-reactifact-run-id"]

    schema = client.get("/openapi.json").json()
    run = schema["paths"]["/run"]["post"]
    assert run["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Question"
    }
    assert run["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Answer"
    }
    assert client.get("/health").json() == {"ok": True}


def test_http_rejects_a_seed_of_the_wrong_shape():
    client = TestClient(_pipeline().http())

    response = client.post("/run", json={"wrong": 1})

    assert response.status_code == 422


def test_http_streams_progress_and_done_events():
    client = TestClient(_pipeline().http())

    response = client.post("/stream", json={"text": "hi"})

    assert response.status_code == 200
    assert "event: progress" in response.text
    assert "event: done" in response.text


def test_http_mounts_contracts_and_traces():
    pipeline = _pipeline(traces=True)
    client = TestClient(pipeline.http())

    assert client.get("/contracts").status_code == 200
    contracts = client.get("/api/contracts").json()
    assert contracts["seed"]["title"] == "Question"
    assert contracts["answer"]["title"] == "Answer"
    assert client.get("/traces").status_code == 200
    assert client.get("/api/traces").status_code == 200

    pipeline.close()


def test_http_route_paths_can_be_overridden():
    app = _pipeline().http(
        prefix="/api",
        routes=PipelineRoutes(run="/chat", stream="/chat/stream"),
    )
    client = TestClient(app)

    assert client.post("/api/chat", json={"text": "hi"}).json() == {"text": "HI"}
    assert "event: done" in client.post("/api/chat/stream", json={"text": "hi"}).text
    assert client.post("/api/run", json={"text": "hi"}).status_code == 404


def test_http_needs_a_typed_pipeline():
    pipeline = Pipeline(steps=[answer])

    with pytest.raises(ValueError):
        pipeline.http()


def test_http_fails_fast_on_a_broken_contract():
    @step(consumes=Answer)
    def broken(answer: Answer) -> Question:
        return Question(text=answer.text)

    pipeline = Pipeline(
        steps=[broken],
        seed_type=Question,
        answer_type=Question,
    )

    with pytest.raises(RuntimeError, match="contracts are invalid"):
        pipeline.http()
