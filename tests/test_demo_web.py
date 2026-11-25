"""`reactifact demo --web` — the in-memory demo app."""

from __future__ import annotations

from fastapi.testclient import TestClient
from reactifact.demo_web import build_demo_app


def test_demo_web_serves_answer_contracts_and_traces():
    client = TestClient(build_demo_app())

    page = client.get("/")
    assert page.status_code == 200
    assert "Q2 cloud spend was" in page.text
    assert "Provenance" in page.text
    assert page.headers["x-request-id"]

    assert client.get("/contracts").status_code == 200
    assert client.get("/traces").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_demo_web_api_is_reproducible_and_has_a_trace():
    client = TestClient(build_demo_app())

    payload = client.get("/api/demo").json()

    assert payload["reproducible"] is True
    assert payload["hash"] == payload["rerun_hash"]
    assert payload["trace_id"]
    assert payload["answer"]["text"].startswith("Q2 cloud spend was")
    assert payload["spend"]["total"] == 45000
    assert payload["variance"]["within_policy"] is False
