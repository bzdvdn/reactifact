"""`install_error_handlers` maps reactifact domain errors to HTTP."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reactifact.authz import AuthorizationError
from reactifact.branching import MergeConflict
from reactifact.guardrails import GuardrailViolation
from reactifact.quota import QuotaExceeded, Usage
from reactifact.web import install_error_handlers


@pytest.mark.parametrize(
    ("error", "status", "name"),
    [
        (AuthorizationError("run", "k8s"), 403, "AuthorizationError"),
        (GuardrailViolation("pii", "blocked"), 422, "GuardrailViolation"),
        (QuotaExceeded("tenant", "max_calls", Usage(calls=10)), 429, "QuotaExceeded"),
        (MergeConflict("diverged"), 409, "MergeConflict"),
    ],
)
def test_domain_errors_become_json(error, status, name):
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise error

    response = TestClient(app).get("/boom")
    assert response.status_code == status
    body = response.json()
    assert body["error"] == name
    assert body["detail"]


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
    assert response.json()["error"] == "Teapot"
