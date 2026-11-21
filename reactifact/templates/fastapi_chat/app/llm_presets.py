"""Vendor presets for the app's LLM configuration.

Presets are *data*: a model id is never hardcoded (users must set
`APP_LLM_MODEL`), and every OpenAI-compatible vendor is one `OpenAICompatProvider`
behind a base URL + key env. Add a vendor by adding one row here or use
`APP_LLM_PROVIDER=custom` with an explicit base URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderAPI = Literal["openai_compatible", "anthropic"]


@dataclass(frozen=True)
class ProviderPreset:
    api: ProviderAPI
    base_url: str
    api_key_env: str | None
    auth_header: str = "Authorization"
    auth_scheme: str | None = "Bearer"


PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset("openai_compatible", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ProviderPreset(
        "openai_compatible", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"
    ),
    "deepseek": ProviderPreset(
        "openai_compatible", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"
    ),
    "groq": ProviderPreset("openai_compatible", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "mistral": ProviderPreset("openai_compatible", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "xai": ProviderPreset("openai_compatible", "https://api.x.ai/v1", "XAI_API_KEY"),
    "together": ProviderPreset(
        "openai_compatible", "https://api.together.xyz/v1", "TOGETHER_API_KEY"
    ),
    "fireworks": ProviderPreset(
        "openai_compatible",
        "https://api.fireworks.ai/inference/v1",
        "FIREWORKS_API_KEY",
    ),
    "cerebras": ProviderPreset(
        "openai_compatible", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"
    ),
    "ollama": ProviderPreset("openai_compatible", "http://localhost:11434/v1", None),
    "litellm": ProviderPreset(
        "openai_compatible", "http://localhost:4000/v1", "LITELLM_MASTER_KEY"
    ),
    "vllm": ProviderPreset("openai_compatible", "http://localhost:8000/v1", None),
    "lmstudio": ProviderPreset("openai_compatible", "http://localhost:1234/v1", None),
    "anthropic": ProviderPreset(
        "anthropic",
        "https://api.anthropic.com/v1",
        "ANTHROPIC_API_KEY",
        auth_header="x-api-key",
        auth_scheme=None,
    ),
}

__all__ = ["PRESETS", "ProviderAPI", "ProviderPreset"]
