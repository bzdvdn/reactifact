"""`install_error_handlers` maps reactifact domain errors to HTTP."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reactifact.authz import AuthorizationError
from reactifact.branching import MergeConflict
from reactifact.guardrails import GuardrailViolation
from reactifact.quota import QuotaExceeded, Usage
from reactifact.web import install_error_handlers, install_request_id


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (AuthorizationError("run", "k8s"), 403, "authorization_error"),
        (GuardrailViolation("pii", "blocked"), 422, "guardrail_violation"),
        (QuotaExceeded("tenant", "max_calls", Usage(calls=10)), 429, "quota_exceeded"),
        (MergeConflict("diverged"), 409, "merge_conflict"),
    ],
)
def test_domain_errors_become_json(error, status, code):
    app = FastAPI()
    install_request_id(app)
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise error

    response = TestClient(app).get("/boom", headers={"X-Request-ID": "req-1"})
    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert body["error"]["request_id"] == "req-1"
    assert isinstance(body["error"]["trace_id"], str)
    assert "detail" not in body
    assert response.headers["X-Request-ID"] == "req-1"


def test_error_envelope_accepts_a_run_id_header():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise AuthorizationError("run", "k8s")

    response = TestClient(app).get("/boom", headers={"X-Reactifact-Run-Id": "run-7"})

    assert response.json()["error"]["trace_id"] == "run-7"
    assert response.headers["X-Reactifact-Run-Id"] == "run-7"


def test_extra_mapping_overrides_the_default():
    class Teapot(Exception):
        pass

    app = FastAPI()
    install_error_handlers(app, extra={Teapot: 418})

    @app.get("/tea")
    def tea() -> None:
        raise Teapot("short and stout")

    response = TestClient(app).get("/tea")
    assert response.status_code == 418
    assert response.json()["error"]["code"] == "teapot"
