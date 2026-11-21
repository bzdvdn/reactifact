"""Daemon settings — budgets, LLM and loop timing are configuration, not code."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm_presets import PRESETS, ProviderAPI


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"
    service_name: str = "reactifact-daemon"

    # ── loop ────────────────────────────────────────────────────────────
    interval_seconds: float = 60.0
    run_once: bool = False

    # ── LLM ─────────────────────────────────────────────────────────────
    llm_provider: str = "none"
    llm_api: ProviderAPI = "openai_compatible"
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key_env: str | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1024
    llm_timeout: float = 60.0
    llm_proxy: str | None = None
    llm_retry_attempts: int = 3
    llm_auth_header: str | None = None
    llm_auth_scheme: str | None = "Bearer"
    llm_extra_headers: dict[str, str] = {}
    llm_extra_body: dict[str, object] = {}
    llm_stream_usage: bool = True

    # ── budget (per tick) ───────────────────────────────────────────────
    budget_max_runs: int | None = 20
    budget_max_iterations: int | None = 10
    budget_max_seconds: float | None = 60.0
    budget_max_tool_calls: int | None = 4
    budget_max_tokens: int | None = 20_000
    budget_max_cost: float | None = 0.10

    # ── tracing/agent ───────────────────────────────────────────────────
    tracing_enabled: bool = True
    trace_path: str = "./data/traces.db"
    agent_timeout_seconds: float | None = 60.0
    principal_id: str = "daemon"

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
        if self.llm_provider in {"custom", "openai_compatible"} and (
            not self.llm_base_url or not self.llm_model
        ):
            raise ValueError("custom provider needs APP_LLM_BASE_URL and model")
        if self.env == "prod" and self.llm_api_key_env and not os.getenv(self.llm_api_key_env):
            raise ValueError(f"{self.llm_api_key_env} is required in prod")
        if self.interval_seconds <= 0 and not self.run_once:
            raise ValueError("APP_INTERVAL_SECONDS must be > 0")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
