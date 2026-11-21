from __future__ import annotations

from app.main import create_app
from app.models import User
from app.security import get_current_user
from fastapi.testclient import TestClient
from reactifact.providers import FakeLLM


def test_one_user_cannot_read_or_clear_another_users_session(settings, monkeypatch):
    monkeypatch.setattr(
        "app.agent.resources.build_llm",
        lambda settings: FakeLLM('{"text":"private answer"}'),
    )
    app = create_app(settings)
    app.dependency_overrides[get_current_user] = lambda: User(id="alice", username="alice")
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat/stream",
            json={"message": "secret", "session_id": "shared"},
        ):
            pass

        app.dependency_overrides[get_current_user] = lambda: User(id="bob", username="bob")
        bob_history = client.get("/api/chat/history", params={"session_id": "shared"}).json()
        assert bob_history["messages"] == []

        client.delete("/api/chat/session", params={"session_id": "shared"})

        app.dependency_overrides[get_current_user] = lambda: User(id="alice", username="alice")
        alice_history = client.get("/api/chat/history", params={"session_id": "shared"}).json()
        assert [m["role"] for m in alice_history["messages"]] == [
            "user",
            "assistant",
        ]
