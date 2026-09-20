"""Anthropic — Messages API (not OpenAI-compatible: a separate contract)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .._httpx import LoopBoundClient
from ._retry import with_retry
from .contracts import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    auth_value,
)


class AnthropicProvider(LLMProvider):
    """Provider for the Anthropic Messages API (/v1/messages, x-api-key).

    Auth defaults to the Anthropic convention (`x-api-key` header, raw key) but
    is configurable like the other providers (auth_header/auth_scheme/proxy).
    `response_format` does not exist in Anthropic — structured output is obtained
    via plain JSON in the text (the tolerant parse_structured covers this).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-latest",
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 90.0,
        transport: Any | None = None,
        extra_headers: dict[str, str] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        proxy: str | None = None,
        auth_header: str = "x-api-key",
        auth_scheme: str | None = None,
        retry_attempts: int = 3,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Anthropic's Messages API requires `max_tokens`; 4096 is the library
        # default. Pass an explicit value to change it, or override per call.
        self.max_tokens = max_tokens if max_tokens is not None else 4096
        self.temperature = temperature
        self.retry_attempts = retry_attempts
        self._headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            **(extra_headers or {}),
        }
        self._headers.setdefault(auth_header.lower(), auth_value(api_key, auth_scheme))
        self._transport = transport
        self._proxy = proxy
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
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        messages = [
            {"role": m.role, "content": m.content}
            for m in request.messages
            if m.role in ("user", "assistant")
        ]
        if not messages:
            messages = [{"role": "user", "content": ""}]
        temperature = (
            request.temperature if request.temperature is not None else self.temperature
        )
        payload: dict[str, Any] = {
            "model": request.extra.get("model") or self.model,
            "max_tokens": (
                request.max_tokens
                if request.max_tokens is not None
                else self.max_tokens
            ),
            "messages": messages,
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if system:
            payload["system"] = system
        if request.stop:
            payload["stop_sequences"] = request.stop
        for key, value in request.extra.items():
            if key != "model":
                payload[key] = value
        return payload

    async def complete(self, request: LLMRequest) -> LLMResponse:
        async def _call() -> LLMResponse:
            response = await self._get_client().post(
                f"{self.base_url}/messages",
                json=self._payload(request, stream=False),
            )
            response.raise_for_status()
            data = response.json()
            text = "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )
            return LLMResponse(
                text=text,
                raw=data,
                finish_reason=data.get("stop_reason"),
                usage=data.get("usage", {}),
            )

        return await with_retry(_call, attempts=self.retry_attempts)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        async with self._get_client().stream(
            "POST",
            f"{self.base_url}/messages",
            json=self._payload(request, stream=True),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    blob = json.loads(data)
                except ValueError:
                    continue
                if blob.get("type") == "content_block_delta":
                    delta = blob.get("delta", {})
                    text = delta.get("text")
                    if text:
                        yield LLMResponseChunk(text=text)

    async def aclose(self) -> None:
        await self._http.aclose()


def anthropic_llm(
    api_key: str | None = None,
    model: str = "claude-3-5-sonnet-latest",
    **kwargs: Any,
) -> AnthropicProvider | None:
    """Builds an Anthropic provider (key from ANTHROPIC_API_KEY).

    Optional knobs in env or kwargs: ANTHROPIC_PROXY / proxy,
    ANTHROPIC_AUTH_HEADER / auth_header (default x-api-key),
    ANTHROPIC_AUTH_SCHEME / auth_scheme (default raw key).
    """
    import os

    if api_key is None:
        api_key = kwargs.get("api_key") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    kwargs.setdefault("proxy", os.getenv("ANTHROPIC_PROXY") or None)
    kwargs.setdefault(
        "auth_header",
        os.getenv("ANTHROPIC_AUTH_HEADER") or "x-api-key",
    )
    scheme = kwargs.get("auth_scheme") or os.getenv("ANTHROPIC_AUTH_SCHEME")
    kwargs["auth_scheme"] = scheme if scheme else None
    return AnthropicProvider(api_key=api_key, model=model, **kwargs)
