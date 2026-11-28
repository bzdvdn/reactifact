"""HTTP `Idempotency-Key` on the typed pipeline router."""

from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Pipeline,
    PipelineRunner,
    SessionStore,
    step,
)
from reactifact.checkpoints import InMemoryKVBackend
from reactifact.web import create_pipeline_router

RUNS = 0


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@step(reads=Question, produces=Answer)
def answer(question: Question) -> Answer:
    global RUNS
    RUNS += 1
    return Answer(text=question.text.upper())


def _client(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _runner() -> PipelineRunner:
    pipeline = Pipeline(
        agents=[Agent("answer", consumes=[Consume(Question)], steps=[answer])],
        seed_type=Question,
        answer_type=Answer,
        name="qa",
    )
    return PipelineRunner(pipeline)


def test_completed_key_replays_the_stored_response():
    global RUNS
    RUNS = 0
    store = SessionStore(InMemoryKVBackend())
    client = _client(create_pipeline_router(_runner(), invocations=store))

    first = client.post(
        "/run", json={"text": "hello"}, headers={"Idempotency-Key": "k1"}
    )
    second = client.post(
        "/run", json={"text": "hello"}, headers={"Idempotency-Key": "k1"}
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json() == {"text": "HELLO"}
    assert RUNS == 1
    assert second.headers["Idempotent-Replayed"] == "true"


def test_running_key_returns_409():
    store = SessionStore(InMemoryKVBackend())
    runner = _runner()
    asyncio.run(
        store.put_invocation(
            f"{runner.name}:busy",
            {
                "status": "running",
                "claim_id": "other",
                "started_at": time.time(),
            },
        )
    )
    client = _client(create_pipeline_router(runner, invocations=store))

    response = client.post(
        "/run", json={"text": "hello"}, headers={"Idempotency-Key": "busy"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invocation_running"


def test_failed_key_can_be_retried():
    global RUNS
    RUNS = 0
    store = SessionStore(InMemoryKVBackend())
    runner = _runner()
    asyncio.run(
        store.put_invocation(
            f"{runner.name}:retry",
            {"status": "failed", "claim_id": "old"},
        )
    )
    client = _client(create_pipeline_router(runner, invocations=store))

    response = client.post(
        "/run", json={"text": "hello"}, headers={"Idempotency-Key": "retry"}
    )

    assert response.status_code == 200
    assert response.json() == {"text": "HELLO"}
    assert RUNS == 1


def test_stream_rejects_idempotency_keys():
    store = SessionStore(InMemoryKVBackend())
    client = _client(create_pipeline_router(_runner(), invocations=store))

    response = client.post(
        "/stream", json={"text": "hello"}, headers={"Idempotency-Key": "k1"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_not_supported"


def test_without_a_key_each_request_runs():
    global RUNS
    RUNS = 0
    client = _client(create_pipeline_router(_runner()))

    client.post("/run", json={"text": "a"})
    client.post("/run", json={"text": "a"})

    assert RUNS == 2


def test_key_without_a_store_is_rejected():
    client = _client(create_pipeline_router(_runner()))

    response = client.post(
        "/run", json={"text": "hello"}, headers={"Idempotency-Key": "k1"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_not_configured"


def test_reusing_a_key_with_a_different_body_is_rejected():
    global RUNS
    RUNS = 0
    store = SessionStore(InMemoryKVBackend())
    client = _client(create_pipeline_router(_runner(), invocations=store))

    first = client.post(
        "/run", json={"text": "hello"}, headers={"Idempotency-Key": "same"}
    )
    second = client.post(
        "/run", json={"text": "different"}, headers={"Idempotency-Key": "same"}
    )

    assert first.status_code == 200
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "idempotency_key_reused"
    assert RUNS == 1
