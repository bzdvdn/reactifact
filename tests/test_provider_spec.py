"""`LLMSpec`: declarative provider construction with honest fallbacks."""

from __future__ import annotations

import pytest
from reactifact.providers import LLMSpec, OpenAICompatProvider


def _reasons() -> tuple[list[str], object]:
    reasons: list[str] = []

    def on_error(reason: str, _exc: BaseException | None) -> None:
        reasons.append(reason)

    return reasons, on_error


def test_openai_without_key_returns_none_and_reports(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reasons, on_error = _reasons()
    assert LLMSpec(provider="openai").build(on_error=on_error) is None
    assert reasons == ["missing_key"]


def test_explicit_key_builds_openai_provider():
    provider = LLMSpec(provider="openai", api_key="sk-test").build()
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.api_key == "sk-test"
    assert provider.model == "gpt-4o-mini"


def test_api_key_env_is_resolved(monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "sk-env")
    provider = LLMSpec(
        provider="openai",
        api_key_env="TEST_LLM_KEY",
    ).build()
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.api_key == "sk-env"


def test_missing_explicit_env_key_does_not_fall_back_to_openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-use")
    monkeypatch.delenv("MISSING_LLM_KEY", raising=False)
    reasons, on_error = _reasons()
    assert (
        LLMSpec(provider="openai", api_key_env="MISSING_LLM_KEY").build(
            on_error=on_error
        )
        is None
    )
    assert reasons == ["missing_key"]


def test_anonymous_local_endpoint_builds_without_auth():
    provider = LLMSpec(
        provider="openai-compatible",
        base_url="http://localhost:11434/v1",
        model="llama3",
        allow_anonymous=True,
    ).build()
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.api_key is None
    assert provider.model == "llama3"


def test_compatible_endpoint_requires_a_key_or_anonymous():
    reasons, on_error = _reasons()
    provider = LLMSpec(
        provider="openai-compatible",
        base_url="http://localhost:11434/v1",
    ).build(on_error=on_error)
    assert provider is None
    assert reasons == ["missing_key"]

    reasons, on_error = _reasons()
    provider = LLMSpec(provider="openai-compatible").build(on_error=on_error)
    assert provider is None
    assert reasons == ["missing_base_url"]


def test_unknown_provider_is_reported():
    reasons, on_error = _reasons()
    assert LLMSpec(provider="nope").build(on_error=on_error) is None
    assert reasons == ["unknown_provider"]


def test_api_key_is_not_in_repr():
    assert "sk-secret" not in repr(LLMSpec(provider="openai", api_key="sk-secret"))


def test_invalid_values_raise():
    with pytest.raises(ValueError):
        LLMSpec(timeout=0)
    with pytest.raises(ValueError):
        LLMSpec(retry_attempts=0)
    with pytest.raises(ValueError):
        LLMSpec(provider=" ")


def test_openrouter_uses_its_defaults_and_fast_mode():
    provider = LLMSpec(provider="openrouter", api_key="sk-or").build()
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.model == "deepseek/deepseek-v4-flash"
    assert provider._extra_body == {"reasoning": {"enabled": False}}


def test_openai_anonymous_uses_the_compatible_client():
    provider = LLMSpec(
        provider="openai",
        base_url="http://localhost:8000/v1",
        allow_anonymous=True,
    ).build()
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "http://localhost:8000/v1"
    assert provider.api_key is None


def test_anthropic_uses_its_native_defaults(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = LLMSpec(
        provider="anthropic",
        api_key="sk-ant",
        model="claude-sonnet-4",
    ).build()
    from reactifact.providers import AnthropicProvider

    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-4"
    assert provider.base_url == "https://api.anthropic.com/v1"
    assert provider._headers["x-api-key"] == "sk-ant"


def test_anthropic_without_key_is_reported(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reasons, on_error = _reasons()
    assert LLMSpec(provider="anthropic").build(on_error=on_error) is None
    assert reasons == ["missing_key"]


def test_anthropic_can_override_auth_header(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = LLMSpec(
        provider="anthropic",
        api_key="sk-ant",
        auth_header="X-Custom-Key",
        auth_scheme="Token",
    ).build()
    from reactifact.providers import AnthropicProvider

    assert isinstance(provider, AnthropicProvider)
    assert provider._headers["x-custom-key"] == "Token sk-ant"


@pytest.mark.parametrize(
    "name",
    [
        "openai",
        "openrouter",
        "anthropic",
        "gemini",
        "cerebras",
        "deepseek",
        "fireworks",
        "github-models",
        "groq",
        "mistral",
        "nvidia",
        "perplexity",
        "qwen",
        "together",
        "xai",
        "zai",
    ],
)
def test_every_chat_factory_provider_builds(name):
    provider = LLMSpec(provider=name, api_key="sk-test").build()
    assert provider is not None


def test_azure_builds_from_endpoint_and_deployment():
    provider = LLMSpec(
        provider="azure",
        base_url="https://example.openai.azure.com",
        model="my-deployment",
        api_key="sk-azure",
    ).build()
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "https://example.openai.azure.com"
    assert provider.model == "my-deployment"


def test_ollama_needs_no_key():
    provider = LLMSpec(provider="ollama", model="qwen2.5").build()
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.api_key is None


@pytest.mark.parametrize(
    ("alias", "target"),
    [
        ("claude", "anthropic"),
        ("google", "gemini"),
        ("github", "github-models"),
        ("nvidia-nim", "nvidia"),
    ],
)
def test_provider_aliases_resolve(alias, target):
    a = LLMSpec(provider=alias, api_key="sk-test").build()
    b = LLMSpec(provider=target, api_key="sk-test").build()
    assert type(a) is type(b)
