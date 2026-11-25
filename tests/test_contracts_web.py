"""`create_contracts_router` serves the contracts page, JSON and Markdown."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from reactifact.contracts.web import create_contracts_router

from .test_contracts import GROUPS


def test_contracts_router_serves_html_json_and_markdown():
    app = FastAPI()
    app.include_router(create_contracts_router(GROUPS))
    client = TestClient(app)

    page = client.get("/contracts")
    assert page.status_code == 200
    assert "reactifact contracts" in page.text
    assert "`answer`" not in page.text  # HTML, not Markdown
    assert "search" in page.text
    assert "pointerdown" in page.text  # drag-to-pan
    assert "tree-group" in page.text and "tree-step" in page.text
    assert "contracts-theme" in page.text
    assert "data:image/svg+xml;base64," in page.text

    payload = client.get("/api/contracts").json()
    assert payload["groups"][0]["name"] == "answer"

    markdown = client.get("/contracts.md")
    assert markdown.status_code == 200
    assert "## `answer`" in markdown.text


def test_contracts_router_honours_prefix_and_basic_auth():
    app = FastAPI()
    app.include_router(
        create_contracts_router(GROUPS, prefix="/ai", username="obs", password="pw")
    )
    client = TestClient(app)

    assert client.get("/ai/contracts").status_code == 401
    assert client.get("/ai/api/contracts").status_code == 401

    auth = ("obs", "pw")
    assert client.get("/ai/contracts", auth=auth).status_code == 200
    assert client.get("/ai/api/contracts", auth=auth).json()["groups"]
    assert client.get("/ai/contracts.md", auth=auth).text.startswith("# reactifact")
