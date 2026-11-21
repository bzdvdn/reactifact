from __future__ import annotations

import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        env="dev",
        auth_token=SecretStr("test-token"),
        auth_user_id="demo",
        llm_provider="none",
        session_backend="memory",
        trace_path=str(tmp_path / "traces.db"),
        chat_memory_keep=20,
        chat_memory_commits=50,
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}
