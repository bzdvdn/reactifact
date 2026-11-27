"""Declarative LLM provider specs: one value -> one provider or `None`.

Vendor factories (`openai_llm`, `anthropic_llm`, `deepseek_llm`, …) are
convenient but every app ends up re-deriving the same branch: which factory,
default base URL, proxy, token env, anonymous local endpoint, and what to do
when the key is missing. `LLMSpec` names that shape once and keeps the failure
honest: `build()` returns `None` (the app runs its fallback) and reports why
through `on_error`.

The module is intentionally vendor-free: concrete providers are imported only
inside `LLMSpec.build`, so `import reactifact.providers` stays cheap.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Literal, cast

from .contracts import LLMProvider

#: `(reason, exception)` where reason is one of `"missing_key"`,
#: `"missing_base_url"`, `"unknown_provider"` or `"build_error"`.
ErrorHandler = Callable[[str, BaseException | None], None]

_ProviderStyle = Literal["factory", "azure"]


@dataclass(frozen=True, slots=True)
class _Provider:
    """How one public provider name maps to a lazy factory."""

    module: str
    factory: str
    style: _ProviderStyle = "factory"
    #: The factory builds without a key (a local endpoint such as Ollama).
    key_required: bool = True
    #: The provider accepts this optional kwarg; native APIs differ.
    extra_headers: bool = True
    extra_body: bool = True
    stream_usage: bool = True


#: Chat providers with a dedicated `*_llm` factory. The list is explicit so
#: `LLMSpec` never resolves an arbitrary attribute from the package.
_FACTORY_PROVIDERS: dict[str, _Provider] = {
    "openai": _Provider("openai", "openai_llm"),
    "openrouter": _Provider("openrouter", "openrouter_llm"),
    "anthropic": _Provider(
        "anthropic",
        "anthropic_llm",
        extra_body=False,
        stream_usage=False,
    ),
    "gemini": _Provider(
        "gemini",
        "gemini_llm",
        extra_headers=False,
        extra_body=False,
        stream_usage=False,
    ),
    "azure": _Provider("azure", "azure_llm", style="azure"),
    "ollama": _Provider("ollama", "ollama_llm", key_required=False),
    "cerebras": _Provider("cerebras", "cerebras_llm"),
    "deepseek": _Provider("deepseek", "deepseek_llm"),
    "fireworks": _Provider("fireworks", "fireworks_llm"),
    "github-models": _Provider("github_models", "github_models_llm"),
    "groq": _Provider("groq", "groq_llm"),
    "mistral": _Provider("mistral", "mistral_llm"),
    "nvidia": _Provider("nvidia", "nvidia_nim_llm"),
    "perplexity": _Provider("perplexity", "perplexity_llm"),
    "qwen": _Provider("qwen", "qwen_llm"),
    "together": _Provider("together", "together_llm"),
    "xai": _Provider("xai", "xai_llm"),
    "zai": _Provider("zai", "zai_llm"),
}

_PROVIDER_ALIASES = {
    "claude": "anthropic",
    "google": "gemini",
    "github": "github-models",
    "github-models": "github-models",
    "nvidia-nim": "nvidia",
    "nvidia-nim-llm": "nvidia",
}

_COMPATIBLE_NAMES = {
    "openai-compatible",
    "compatible",
    "local",
    "vllm",
    "lmstudio",
}

_COMPATIBLE = _Provider("", "")


@dataclass(frozen=True, slots=True)
class LLMSpec:
    """A provider description that builds to `LLMProvider | None`.

        llm = LLMSpec(
            provider="anthropic",
            model="claude-sonnet-4",
            api_key_env="ANTHROPIC_API_KEY",
            max_tokens=4096,
        ).build(on_error=lambda reason, exc: ...)

    `provider` covers every chat provider in `reactifact.providers`: `openai`,
    `openrouter`, `anthropic`, `gemini`, `azure`, `ollama`, `cerebras`,
    `deepseek`, `fireworks`, `github-models`, `groq`, `mistral`, `nvidia`,
    `perplexity`, `qwen`, `together`, `xai`, `zai`, plus the generic
    `openai-compatible`/`compatible`/`local`/`vllm`/`lmstudio` endpoint.

    Provider-native defaults (model, base URL, auth header/scheme, Anthropic's
    `x-api-key`, Gemini's `x-goog-api-key`) stay in the factory unless the
    matching spec fields are set explicitly. `api_key` is excluded from `repr`;
    `api_key_env` names the variable. `build()` never raises for provider
    availability — missing config calls `on_error` and returns `None`.
    """

    provider: str = "openai"
    model: str | None = None
    base_url: str | None = None

    api_key: str | None = field(default=None, repr=False)
    api_key_env: str | None = None

    proxy: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    timeout: float = 60.0
    retry_attempts: int = 3

    #: Native providers keep their own defaults; set these only to override.
    auth_header: str | None = None
    auth_scheme: str | None = None
    #: Send the key raw (`scheme=None`) with the provider's header.
    raw_key: bool = False

    extra_headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    stream_usage: bool = True

    #: A local/self-hosted endpoint that accepts requests without a key.
    allow_anonymous: bool = False

    def __post_init__(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValueError("LLMSpec.provider must be a non-empty string")
        if self.timeout <= 0:
            raise ValueError("LLMSpec.timeout must be > 0")
        if self.retry_attempts < 1:
            raise ValueError("LLMSpec.retry_attempts must be >= 1")
        if self.api_key_env is not None and not self.api_key_env.strip():
            raise ValueError("LLMSpec.api_key_env must be a non-empty string")
        # Freeze the mutable mappings without making the dataclass unsafe to
        # share: a caller mutating the original dict must not change the spec.
        object.__setattr__(self, "extra_headers", dict(self.extra_headers))
        object.__setattr__(self, "extra_body", dict(self.extra_body))

    def build(
        self,
        *,
        on_error: ErrorHandler | None = None,
    ) -> LLMProvider | None:
        """Constructs the provider, or returns `None` and reports why.

        Invalid *values* (empty provider, non-positive timeout) raise
        `ValueError` from the constructor; unavailable *runtime* configuration
        (missing key/base URL, unknown provider) calls `on_error` and returns
        `None`. Factory exceptions are reported as `"build_error"` and also
        return `None`.
        """
        name = self.provider.strip().lower().replace("_", "-")
        name = _PROVIDER_ALIASES.get(name, name)
        try:
            if name in _COMPATIBLE_NAMES:
                return self._build_compatible(on_error)
            if name == "openai" and self.allow_anonymous:
                return self._make_compatible(
                    base_url=self.base_url or "https://api.openai.com/v1",
                    model=self.model or "gpt-4o-mini",
                    on_error=on_error,
                )
            provider = _FACTORY_PROVIDERS.get(name)
            if provider is None:
                self._report(on_error, "unknown_provider", None)
                return None
            if provider.style == "azure":
                return self._build_azure(provider, on_error)
            return self._build_factory(provider, on_error)
        except Exception as exc:  # noqa: BLE001 — availability is an outcome
            self._report(on_error, "build_error", exc)
            return None

    # -- provider branches ------------------------------------------------- #

    def _build_factory(
        self, provider: _Provider, on_error: ErrorHandler | None
    ) -> LLMProvider | None:
        key = self._key()
        if self._has_explicit_key and key is None:
            self._report(on_error, "missing_key", None)
            return None
        module = import_module(f".{provider.module}", __package__)
        factory = getattr(module, provider.factory)
        kwargs = self._provider_kwargs(provider)
        if self.model is not None:
            kwargs["model"] = self.model
        if self.base_url is not None:
            kwargs["base_url"] = self.base_url
        built = factory(api_key=key, **kwargs)
        if built is None:
            self._report(on_error, "missing_key", None)
            return None
        if (
            provider.key_required
            and not self.allow_anonymous
            and getattr(built, "api_key", None) is None
        ):
            self._report(on_error, "missing_key", None)
            return None
        return cast(LLMProvider, built)

    def _build_azure(
        self, provider: _Provider, on_error: ErrorHandler | None
    ) -> LLMProvider | None:
        if not self.base_url:
            self._report(on_error, "missing_base_url", None)
            return None
        key = self._key()
        if self._has_explicit_key and key is None:
            self._report(on_error, "missing_key", None)
            return None
        from .azure import azure_llm

        kwargs = self._provider_kwargs(provider)
        if self.model is not None:
            kwargs["deployment"] = self.model
        built = azure_llm(endpoint=self.base_url, api_key=key, **kwargs)
        if not self.allow_anonymous and getattr(built, "api_key", None) is None:
            self._report(on_error, "missing_key", None)
            return None
        return built

    def _build_compatible(self, on_error: ErrorHandler | None) -> LLMProvider | None:
        if not self.base_url:
            self._report(on_error, "missing_base_url", None)
            return None
        return self._make_compatible(
            base_url=self.base_url,
            model=self.model,
            on_error=on_error,
        )

    def _make_compatible(
        self,
        *,
        base_url: str,
        model: str | None,
        on_error: ErrorHandler | None,
    ) -> LLMProvider | None:
        key = self._key()
        if self._has_explicit_key and key is None:
            self._report(on_error, "missing_key", None)
            return None
        if not self.allow_anonymous and key is None:
            # A local endpoint opts into no-auth explicitly; otherwise a missing
            # key is a configuration outcome, not a silent unauthenticated run.
            self._report(on_error, "missing_key", None)
            return None
        from .chat import OpenAICompatProvider

        return OpenAICompatProvider(
            base_url=base_url,
            api_key=key,
            model=model,
            **self._provider_kwargs(_COMPATIBLE),
        )

    # -- helpers ----------------------------------------------------------- #

    @property
    def _has_explicit_key(self) -> bool:
        return self.api_key is not None or bool(self.api_key_env)

    def _key(self) -> str | None:
        if self.api_key is not None:
            return self.api_key or None
        if self.api_key_env:
            return os.getenv(self.api_key_env) or None
        return None

    def _provider_kwargs(self, provider: _Provider) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "proxy": self.proxy,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "retry_attempts": self.retry_attempts,
        }
        if self.auth_header is not None:
            kwargs["auth_header"] = self.auth_header
        if self.raw_key:
            kwargs["auth_scheme"] = None
        elif self.auth_scheme is not None:
            kwargs["auth_scheme"] = self.auth_scheme
        if provider.extra_headers and self.extra_headers:
            kwargs["extra_headers"] = dict(self.extra_headers)
        if provider.extra_body and self.extra_body:
            kwargs["extra_body"] = dict(self.extra_body)
        if provider.stream_usage:
            kwargs["stream_usage"] = self.stream_usage
        return kwargs

    @staticmethod
    def _report(
        on_error: ErrorHandler | None, reason: str, exc: BaseException | None
    ) -> None:
        if on_error is not None:
            on_error(reason, exc)


__all__ = ["LLMSpec"]
