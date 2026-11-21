"""Provider construction from `Settings` — no vendor model defaults."""

from __future__ import annotations

import os

from reactifact.providers import (
    LLMProvider,
    OpenAICompatProvider,
    anthropic_llm,
)

from app.config import Settings


def _api_key(settings: Settings, env_name: str | None) -> str | None:
    return os.getenv(env_name) if env_name else None


def build_llm(settings: Settings) -> LLMProvider | None:
    if settings.llm_provider == "none":
        return None
    if not settings.llm_model:
        raise ValueError("APP_LLM_MODEL is required")
    api_key = _api_key(settings, settings.llm_api_key_env)

    if settings.llm_api_key_env and not api_key:
        # A configured key variable that is empty is a config error, not an
        # invitation to silently run offline.
        raise ValueError(f"{settings.llm_api_key_env} is not set")

    if settings.llm_api == "anthropic":
        return anthropic_llm(
            api_key=api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url or "https://api.anthropic.com/v1",
            timeout=settings.llm_timeout,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            proxy=settings.llm_proxy,
            retry_attempts=settings.llm_retry_attempts,
            extra_headers=settings.llm_extra_headers,
        )

    if not settings.llm_base_url:
        raise ValueError("APP_LLM_BASE_URL is required for openai_compatible")
    return OpenAICompatProvider(
        base_url=settings.llm_base_url,
        api_key=api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
        proxy=settings.llm_proxy,
        retry_attempts=settings.llm_retry_attempts,
        auth_header=settings.llm_auth_header or "Authorization",
        auth_scheme=(settings.llm_auth_scheme if settings.llm_auth_scheme else None),
        extra_headers=settings.llm_extra_headers,
        extra_body=dict(settings.llm_extra_body),
        stream_usage=settings.llm_stream_usage,
    )


__all__ = ["build_llm"]
