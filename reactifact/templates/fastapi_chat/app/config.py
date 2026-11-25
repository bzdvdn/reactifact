"""Application settings — the single source of truth for app wiring.

Every budget, provider/model, store path and chat knob is read from here; the
bootstrap layer never hardcodes a vendor model or a `Budget(...)`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm_presets import PRESETS, ProviderAPI


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: Literal["dev", "prod"] = "dev"
    debug: bool = False
    log_level: str = "INFO"
    cors_origins: str = ""

    # ── auth placeholder (replace with JWT/OIDC in a real app) ──────────
    auth_token: SecretStr | None = None
    auth_user_id: str = "demo"
    auth_username: str = "demo"
    auth_capabilities: str = "user"

    # ── LLM ─────────────────────────────────────────────────────────────
    # A preset name (see app/llm_presets.py), "custom"/"openai_compatible"
    # for an explicit gateway, or "none" for offline/fallback mode.
    llm_provider: str = "none"
    llm_api: ProviderAPI = "openai_compatible"
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key_env: str | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = 2048
    llm_timeout: float = 60.0
    llm_proxy: str | None = None
    llm_retry_attempts: int = 3
    llm_auth_header: str | None = None
    llm_auth_scheme: str | None = "Bearer"
    llm_extra_headers: dict[str, str] = {}
    llm_extra_body: dict[str, object] = {}
    llm_stream_usage: bool = True

    # ── Budget (per turn) ───────────────────────────────────────────────
    budget_max_runs: int | None = 120
    budget_max_iterations: int | None = 100
    budget_max_seconds: float | None = 120.0
    budget_max_tool_calls: int | None = 12
    budget_max_tokens: int | None = 120_000
    budget_max_cost: float | None = 0.50

    # ── Agents / chat ───────────────────────────────────────────────────
    agent_timeout_seconds: float | None = 60.0
    chat_max_concurrency: int = 4
    chat_memory_keep: int | None = 20
    chat_memory_commits: int | None = 500
    chat_session_save_policy: Literal["per_commit", "per_turn"] = "per_commit"
    fallback_reply: str = "No answer was produced. Configure APP_LLM_* or check the server logs."
    tracing_enabled: bool = True
    contracts_enabled: bool = True

    # ── Stores ──────────────────────────────────────────────────────────
    session_backend: Literal["memory", "file", "sqlite", "postgres"] = "sqlite"
    session_path: str = "./data/sessions.db"
    session_url: str | None = None
    trace_backend: Literal["sqlite"] = "sqlite"
    trace_path: str = "./data/traces.db"

    @model_validator(mode="after")
    def apply_provider_preset(self) -> Settings:
        provider = self.llm_provider.strip().lower()
        self.llm_provider = provider
        if provider == "none":
            return self
        if provider in {"custom", "openai_compatible"}:
            if not self.llm_model:
                raise ValueError("APP_LLM_MODEL is required for a custom provider")
            if not self.llm_base_url:
                raise ValueError("APP_LLM_BASE_URL is required for a custom provider")
            return self
        preset = PRESETS.get(provider)
        if preset is None:
            raise ValueError(
                f"unknown llm_provider={provider!r}; use one of {sorted(PRESETS)} or 'custom'"
            )
        self.llm_api = preset.api
        self.llm_base_url = self.llm_base_url or preset.base_url
        self.llm_api_key_env = self.llm_api_key_env or preset.api_key_env
        self.llm_auth_header = preset.auth_header
        self.llm_auth_scheme = preset.auth_scheme
        if not self.llm_model:
            raise ValueError(f"APP_LLM_MODEL is required for {provider!r}")
        return self

    def validate_runtime(self) -> None:
        """Fail fast on configurations that must not reach production."""
        if self.auth_token is None:
            raise ValueError("APP_AUTH_TOKEN must be set (replace with real auth)")
        if self.llm_provider not in {"none", "custom", "openai_compatible"} and not self.llm_model:
            raise ValueError("APP_LLM_MODEL is required")
        if self.llm_provider in {"custom", "openai_compatible"}:
            if not self.llm_base_url:
                raise ValueError("a custom provider needs APP_LLM_BASE_URL")
            if not self.llm_model:
                raise ValueError("APP_LLM_MODEL is required")
        if self.env == "prod":
            if self.session_backend == "memory":
                raise ValueError("session_backend=memory is not allowed in prod")
            if self.trace_backend == "sqlite" and self.trace_path == ":memory:":
                raise ValueError("trace_backend=:memory: is not allowed in prod")
            if self.llm_api_key_env and not os.getenv(self.llm_api_key_env):
                raise ValueError(
                    f"{self.llm_api_key_env} is required for "
                    f"llm_provider={self.llm_provider!r} in prod"
                )

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(c.strip() for c in self.auth_capabilities.split(",") if c.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [c.strip() for c in self.cors_origins.split(",") if c.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
