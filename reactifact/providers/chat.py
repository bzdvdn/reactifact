"""Chat and embeddings: OpenAI-compatible providers (OpenAI, Ollama, vLLM,
Mistral, OpenRouter) and factories from env."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from .._httpx import LoopBoundClient
from ._retry import with_retry
from .contracts import (
    EmbeddingProvider,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    auth_value,
)


class OpenAICompatProvider(LLMProvider):
    """OpenAI-compatible provider: OpenAI, Ollama, vLLM, LM Studio, OpenRouter.

    Auth is fully configurable because vendors disagree:
      - header name:  `Authorization` (default), `X-Api-Key`, etc.
      - key scheme:   `Bearer` (default), `OAuth`, `api-key`, or `None` (raw key).
    `proxy` (a URL) is passed to the httpx client for corporate networks.
    `transport` can receive an httpx transport for tests (MockTransport).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        transport: Any | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        proxy: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str | None = "Bearer",
        temperature: float | None = None,
        max_tokens: int | None = None,
        retry_attempts: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._timeout = timeout
        self._extra_body = dict(extra_body or {})
        self._headers = dict(extra_headers or {})
        if api_key:
            self._headers.setdefault(auth_header, auth_value(api_key, auth_scheme))
        self._transport = transport
        self._proxy = proxy
        self.temperature = temperature
        self.max_tokens = max_tokens
        #: complete()-only retry budget for transient failures (429/5xx/
        #: connection errors, see providers/_retry.py); 1 disables retrying.
        self.retry_attempts = retry_attempts
        self._http = LoopBoundClient(
            lambda: httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                headers=self._headers,
                proxy=self._proxy,
            )
        )

    def _get_client(self) -> httpx.AsyncClient:
        return self._http.get()

    def _payload(self, request: LLMRequest, stream: bool) -> dict[str, Any]:
        # A request-level value overrides the provider default; if neither is
        # set, the field is omitted and the API applies its own default.
        temperature = (
            request.temperature if request.temperature is not None else self.temperature
        )
        max_tokens = (
            request.max_tokens if request.max_tokens is not None else self.max_tokens
        )
        payload: dict[str, Any] = {
            "messages": [
                {"role": m.role, "content": m.content} for m in request.messages
            ],
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        model = request.extra.get("model") or self.model
        if model is not None:
            payload["model"] = model
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if request.stop:
            payload["stop"] = request.stop
        if request.response_format:
            payload["response_format"] = request.response_format
        payload.update(self._extra_body)
        # arbitrary fields (e.g., OpenRouter: {"reasoning": {"enabled": false}})
        for key, value in request.extra.items():
            if key != "model":
                payload[key] = value
        return payload

    async def complete(self, request: LLMRequest) -> LLMResponse:
        async def _call() -> LLMResponse:
            response = await self._get_client().post(
                f"{self.base_url}/chat/completions",
                json=self._payload(request, stream=False),
            )
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            content = choice.get("message", {}).get("content") or ""
            return LLMResponse(
                text=content,
                raw=data,
                finish_reason=choice.get("finish_reason"),
                usage=data.get("usage", {}),
            )

        return await with_retry(_call, attempts=self.retry_attempts)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        async with self._get_client().stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=self._payload(request, stream=True),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                if not data:
                    continue
                chunk_ = json.loads(data)
                delta = chunk_["choices"][0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield LLMResponseChunk(text=text)

    async def aclose(self) -> None:
        await self._http.aclose()


class OpenAICompatEmbedder(EmbeddingProvider):
    """OpenAI-compatible embedding generator (OpenAI, Mistral, OpenRouter)."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        timeout: float = 60.0,
        transport: Any | None = None,
        proxy: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str | None = "Bearer",
        retry_attempts: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers[auth_header] = auth_value(api_key, auth_scheme)
        self._transport = transport
        self._proxy = proxy
        self.retry_attempts = retry_attempts
        self._http = LoopBoundClient(
            lambda: httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                headers=self._headers,
                proxy=self._proxy,
            )
        )

    def _get_client(self) -> httpx.AsyncClient:
        return self._http.get()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async def _call() -> list[list[float]]:
            response = await self._get_client().post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": texts},
            )
            response.raise_for_status()
            data = response.json()
            rows = sorted(data["data"], key=lambda item: item["index"])
            embeddings = [row["embedding"] for row in rows]
            if len(embeddings) != len(texts):
                return []  # fewer rows than requested — honestly empty
            return embeddings

        return await with_retry(_call, attempts=self.retry_attempts)

    async def aclose(self) -> None:
        await self._http.aclose()


def _network_knobs(
    prefix: str, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Provider kwargs for proxy/auth from env (`<PREFIX>_PROXY` /
    `_AUTH_HEADER` / `_AUTH_SCHEME`) or explicit overrides.

    Returns only the knobs that are actually configured, so a provider's own
    default (e.g. Gemini's `x-goog-api-key`) is respected when nothing is set.
    An empty `AUTH_SCHEME` means the raw key (no prefix).
    """
    import os

    ov = overrides or {}
    knobs: dict[str, Any] = {}

    proxy = ov.get("proxy")
    if proxy is None:
        proxy = os.getenv(f"{prefix}_PROXY")
    if proxy is not None:
        knobs["proxy"] = proxy or None

    header = ov.get("auth_header")
    if header is None:
        header = os.getenv(f"{prefix}_AUTH_HEADER")
    if header is not None and header != "":
        knobs["auth_header"] = header

    scheme = ov.get("auth_scheme")
    if scheme is None:
        scheme = os.getenv(f"{prefix}_AUTH_SCHEME")
    if scheme is not None:
        knobs["auth_scheme"] = None if scheme == "" else scheme

    return knobs


def _resolve_env_api_key(explicit: str | None, key_vars: tuple[str, ...]) -> str | None:
    """`explicit` if given, else the first of `key_vars` that's set in env."""
    if explicit is not None:
        return explicit
    import os

    for var in key_vars:
        value = os.getenv(var)
        if value:
            return value
    return None


def _openai_compat_llm(
    *,
    env_prefix: str,
    default_model: str,
    default_base_url: str,
    env_api_key_vars: tuple[str, ...] | None = None,
    name: str | None = None,
    doc: str = "",
) -> Callable[..., OpenAICompatProvider]:
    """Builds a `<vendor>_llm(model=..., base_url=..., api_key=None, **kwargs)`
    factory for a vendor whose API is OpenAI-compatible end to end.

    This is the one implementation behind every same-shaped vendor factory in
    this package (Cerebras, DeepSeek, Fireworks, GitHub Models, Groq, NVIDIA
    NIM, Perplexity, Qwen, Together, xAI, z.ai — see their one-line modules):
    each only differs in `env_prefix`/`default_model`/`default_base_url`, so
    duplicating the body 11 times just means 11 places to fix the same bug in
    (as `llm_from_env`'s dropped-overrides bug was, before it had one home).

    `env_api_key_vars` overrides the single `<PREFIX>_API_KEY` default when a
    vendor's key comes from a differently-named variable (or falls back
    through more than one, e.g. GitHub Models' `GITHUB_TOKEN`/`GITHUB_API_KEY`).
    `name` overrides the `<prefix>_llm` default when the public function name
    doesn't match the prefix (`nvidia_nim_llm`, `github_models_llm`).
    """
    key_vars = env_api_key_vars or (f"{env_prefix}_API_KEY",)

    def factory(
        model: str = default_model,
        base_url: str = default_base_url,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> OpenAICompatProvider:
        merged = {**_network_knobs(env_prefix, kwargs), **kwargs}
        return OpenAICompatProvider(
            base_url=base_url,
            api_key=_resolve_env_api_key(api_key, key_vars),
            model=model,
            **merged,
        )

    factory.__name__ = name or f"{env_prefix.lower()}_llm"
    factory.__qualname__ = factory.__name__
    factory.__doc__ = doc or (
        f"{env_prefix.title()} — OpenAI-compatible chat "
        f"(key from {' or '.join(key_vars)})."
    )
    return factory


def _openai_compat_embedder(
    *,
    env_prefix: str,
    default_model: str,
    default_base_url: str,
    env_api_key_vars: tuple[str, ...] | None = None,
    name: str | None = None,
    doc: str = "",
) -> Callable[..., OpenAICompatEmbedder]:
    """Builds a `<vendor>_embedder(model=..., base_url=..., api_key=None,
    **kwargs)` factory — the embedder counterpart of `_openai_compat_llm`,
    for a vendor whose `/embeddings` endpoint is OpenAI-compatible.
    """
    key_vars = env_api_key_vars or (f"{env_prefix}_API_KEY",)

    def factory(
        model: str = default_model,
        base_url: str = default_base_url,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> OpenAICompatEmbedder:
        merged = {**_network_knobs(env_prefix, kwargs), **kwargs}
        return OpenAICompatEmbedder(
            base_url=base_url,
            api_key=_resolve_env_api_key(api_key, key_vars),
            model=model,
            **merged,
        )

    factory.__name__ = name or f"{env_prefix.lower()}_embedder"
    factory.__qualname__ = factory.__name__
    factory.__doc__ = doc or (
        f"{env_prefix.title()} — OpenAI-compatible embeddings "
        f"(key from {' or '.join(key_vars)})."
    )
    return factory


def llm_from_env(**overrides: Any) -> OpenAICompatProvider | None:
    """Builds a provider from OPENAI_BASE_URL/OPENAI_API_KEY/OPENAI_MODEL.

    OPENAI_EXTRA_BODY (JSON) is added to every request — e.g., for
    OpenRouter: '{"reasoning": {"enabled": false}}'. Optional network/auth
    knobs: OPENAI_PROXY (URL), OPENAI_AUTH_HEADER (default Authorization),
    OPENAI_AUTH_SCHEME (Bearer by default; set to "api-key", "OAuth" or an
    empty value for providers that want the raw key). Returns None if
    BASE_URL is not set — the app runs on its fallbacks.

    Remaining overrides (`temperature`, `max_tokens`, `timeout`, `transport`,
    `extra_headers`) pass straight through to `OpenAICompatProvider` — no
    api_key is required, so this also covers unauthenticated local/self-hosted
    endpoints (Ollama, vLLM, LM Studio).
    """
    import os

    base_url = overrides.get("base_url") or os.getenv("OPENAI_BASE_URL")
    if not base_url:
        return None
    extra_body: dict[str, Any] | None = overrides.get("extra_body")
    if extra_body is None:
        raw = os.getenv("OPENAI_EXTRA_BODY")
        extra_body = json.loads(raw) if raw else None
    consumed = {
        "base_url",
        "api_key",
        "model",
        "extra_body",
        "proxy",
        "auth_header",
        "auth_scheme",
    }
    passthrough = {k: v for k, v in overrides.items() if k not in consumed}
    return OpenAICompatProvider(
        base_url=base_url,
        api_key=overrides.get("api_key") or os.getenv("OPENAI_API_KEY") or None,
        model=overrides.get("model") or os.getenv("OPENAI_MODEL") or None,
        extra_body=extra_body,
        **_network_knobs("OPENAI", overrides),
        **passthrough,
    )


def embedder_from_env(**overrides: Any) -> OpenAICompatEmbedder | None:
    """Builds an embedder from EMBEDDER_BASE_URL/EMBEDDER_API_KEY/EMBEDDER_MODEL.

    Optional knobs: EMBEDDER_PROXY, EMBEDDER_AUTH_HEADER, EMBEDDER_AUTH_SCHEME.
    Remaining overrides (`timeout`, `transport`, `retry_attempts`, ...) pass
    straight through to `OpenAICompatEmbedder`.
    """
    import os

    base_url = overrides.get("base_url") or os.getenv("EMBEDDER_BASE_URL")
    if not base_url:
        return None
    api_key = overrides.get("api_key") or os.getenv("EMBEDDER_API_KEY")
    model = overrides.get("model") or os.getenv("EMBEDDER_MODEL")
    consumed = {"base_url", "api_key", "model", "proxy", "auth_header", "auth_scheme"}
    passthrough = {k: v for k, v in overrides.items() if k not in consumed}
    return OpenAICompatEmbedder(
        base_url=base_url,
        api_key=api_key,
        model=model if isinstance(model, str) else "text-embedding-3-small",
        **_network_knobs("EMBEDDER", overrides),
        **passthrough,
    )
