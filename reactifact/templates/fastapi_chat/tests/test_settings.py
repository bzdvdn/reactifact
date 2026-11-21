from __future__ import annotations

import pytest
from app.bootstrap import build_budget
from app.config import Settings
from app.llm import build_llm
from pydantic import SecretStr


def _settings(**overrides) -> Settings:
    values = {"auth_token": SecretStr("test"), **overrides}
    return Settings(**values)


def test_provider_preset_fills_base_url_and_key_env():
    settings = _settings(llm_provider="groq", llm_model="llama-3.3-70b")
    assert settings.llm_api == "openai_compatible"
    assert settings.llm_base_url == "https://api.groq.com/openai/v1"
    assert settings.llm_api_key_env == "GROQ_API_KEY"


def test_custom_provider_requires_base_url_and_model():
    with pytest.raises(ValueError, match="APP_LLM_MODEL"):
        _settings(llm_provider="custom")
    with pytest.raises(ValueError, match="APP_LLM_BASE_URL"):
        _settings(
            llm_provider="custom",
            llm_model="my-model",
            env="prod",
        ).validate_runtime()


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="unknown llm_provider"):
        _settings(llm_provider="not-a-provider", llm_model="x")


def test_model_is_required_for_a_real_provider():
    with pytest.raises(ValueError, match="APP_LLM_MODEL"):
        _settings(llm_provider="deepseek")


def test_none_provider_is_offline():
    settings = _settings(llm_provider="none")
    assert build_llm(settings) is None


def test_budget_comes_from_settings():
    settings = _settings(
        llm_provider="none",
        budget_max_runs=7,
        budget_max_seconds=3.5,
        budget_max_tool_calls=2,
        budget_max_tokens=99,
        budget_max_cost=0.25,
    )
    budget = build_budget(settings)
    assert budget.max_runs == 7
    assert budget.max_seconds == 3.5
    assert budget.max_tool_calls == 2
    assert budget.max_tokens == 99
    assert budget.max_cost == 0.25


def test_prod_rejects_in_memory_sessions_and_missing_auth():
    with pytest.raises(ValueError, match="APP_AUTH_TOKEN"):
        Settings(env="prod", llm_provider="none").validate_runtime()

    settings = _settings(
        env="prod",
        llm_provider="none",
        session_backend="memory",
    )
    with pytest.raises(ValueError, match="session_backend=memory"):
        settings.validate_runtime()
