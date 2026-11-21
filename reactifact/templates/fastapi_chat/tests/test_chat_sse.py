from __future__ import annotations

from reactifact.providers import FakeLLM


def _fake_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agent.resources.build_llm",
        lambda settings: FakeLLM('{"text":"hello from the template"}'),
    )


def test_chat_sse_returns_a_reply(client, auth_headers, monkeypatch):
    _fake_llm(monkeypatch)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hi", "session_id": "s1"},
        headers=auth_headers,
    ) as response:
        body = "".join(response.iter_text())

    assert "event: session" in body
    assert '"session_id": "s1"' in body
    assert "event: message" in body
    assert "hello from the template" in body


def test_history_replays_the_scoped_session(client, auth_headers, monkeypatch):
    _fake_llm(monkeypatch)
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hi", "session_id": "s2"},
        headers=auth_headers,
    ) as response:
        "".join(response.iter_text())

    state = client.get(
        "/api/chat/history", params={"session_id": "s2"}, headers=auth_headers
    ).json()
    roles = [message["role"] for message in state["messages"]]
    assert roles == ["user", "assistant"]


def test_auth_is_required(client):
    response = client.post(
        "/api/chat/stream",
        json={"message": "hi", "session_id": "s3"},
    )
    assert response.status_code == 401
