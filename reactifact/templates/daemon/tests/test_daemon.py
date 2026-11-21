from __future__ import annotations

import pytest
from app.config import Settings
from app.main import serve
from app.runner import run_once


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        env="dev",
        llm_provider="none",
        run_once=True,
        interval_seconds=0.01,
        trace_path=str(tmp_path / "traces.db"),
    )


def test_run_once_writes_an_offline_report(settings):
    import asyncio

    report = asyncio.run(run_once(settings, 7))
    assert report is not None
    assert report.sequence == 7
    assert "no LLM configured" in report.text
    assert report.source == "offline"


def test_serve_once_stops_after_one_tick(settings):
    import asyncio

    assert asyncio.run(serve(settings)) == 1


def test_provider_preset_and_model_are_settings():
    with pytest.raises(ValueError, match="APP_LLM_MODEL"):
        Settings(llm_provider="deepseek")
    settings = Settings(llm_provider="groq", llm_model="llama-3.3-70b")
    assert settings.llm_base_url == "https://api.groq.com/openai/v1"
    assert settings.llm_api_key_env == "GROQ_API_KEY"
