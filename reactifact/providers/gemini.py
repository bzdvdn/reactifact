"""Google Gemini — native contract (generativelanguage API).

Chat (`generateContent`/`streamGenerateContent`) and image generation (image
modal parts in the same endpoint). Unlike OpenAI-compatible providers, auth is
`x-goog-api-key: <key>` (raw key, no prefix) — but header/scheme and proxy are
configurable like everywhere else.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .._httpx import LoopBoundClient
from ._retry import with_retry
from .chat import _network_knobs
from .contracts import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    auth_value,
)
from .image import ImageProvider

_DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _model_path(model: str) -> str:
    return model if model.startswith("models/") else f"models/{model}"


def _parts_text(parts: list[dict[str, Any]]) -> str:
    return "".join(p.get("text", "") for p in parts if p.get("text"))


class GeminiProvider(LLMProvider):
    """Gemini (Google AI Studio) chat provider, native contract.

    `contents` carries user/model turns; the system instruction is passed
    separately in `systemInstruction`. `response_format` uses
    `generationConfig.response_mime_type` (e.g. application/json).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        base_url: str = _DEFAULT_BASE,
        timeout: float = 120.0,
        transport: Any | None = None,
        proxy: str | None = None,
        auth_header: str = "x-goog-api-key",
        auth_scheme: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        retry_attempts: int = 3,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.retry_attempts = retry_attempts
        self._headers = {"Content-Type": "application/json"}
        self._headers[auth_header] = auth_value(api_key, auth_scheme)
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
        contents: list[dict[str, Any]] = []
        system_parts: list[str] = []
        for m in request.messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m.content}]})
        if not contents:
            contents = [{"role": "user", "parts": [{"text": ""}]}]
        payload: dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n".join(system_parts)}]
            }
        temperature = (
            request.temperature if request.temperature is not None else self.temperature
        )
        max_tokens = (
            request.max_tokens if request.max_tokens is not None else self.max_tokens
        )
        generation: dict[str, Any] = {}
        if temperature is not None:
            generation["temperature"] = temperature
        if max_tokens is not None:
            generation["maxOutputTokens"] = max_tokens
        if request.stop:
            generation["stopSequences"] = request.stop
        if request.response_format:
            generation["response_mime_type"] = "application/json"
        payload["generationConfig"] = generation
        return payload

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        candidate = (data.get("candidates") or [{}])[0]
        parts = candidate.get("content", {}).get("parts", []) or []
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text=_parts_text(parts),
            raw=data,
            finish_reason=candidate.get("finishReason"),
            usage={
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            },
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.extra.get("model") or self.model

        async def _call() -> LLMResponse:
            response = await self._get_client().post(
                f"{self.base_url}/{_model_path(model)}:generateContent",
                json=self._payload(request, stream=False),
            )
            response.raise_for_status()
            return self._parse(response.json())

        return await with_retry(_call, attempts=self.retry_attempts)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        model = request.extra.get("model") or self.model
        async with self._get_client().stream(
            "POST",
            f"{self.base_url}/{_model_path(model)}:streamGenerateContent?alt=sse",
            json=self._payload(request, stream=True),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if not data:
                    continue
                try:
                    blob = _json(data)
                except ValueError:
                    continue
                for part in (
                    (blob.get("candidates") or [{}])[0]
                    .get("content", {})
                    .get("parts", [])
                ):
                    text = part.get("text")
                    if text:
                        yield LLMResponseChunk(text=text)

    async def aclose(self) -> None:
        await self._http.aclose()


class GeminiImageProvider(ImageProvider):
    """Image generation via Gemini's image-modal parts (`inlineData`)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash-exp",
        base_url: str = _DEFAULT_BASE,
        timeout: float = 120.0,
        transport: Any | None = None,
        proxy: str | None = None,
        auth_header: str = "x-goog-api-key",
        auth_scheme: str | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        self._headers[auth_header] = auth_value(api_key, auth_scheme)
        self._transport = transport
        self._proxy = proxy
        self._chat = GeminiProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            proxy=proxy,
            auth_header=auth_header,
            auth_scheme=auth_scheme,
        )

    async def generate(self, prompt: str, **params: Any) -> bytes | None:
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        if params.get("aspect_ratio"):
            payload["generationConfig"]["imageConfig"] = {
                "aspectRatio": params["aspect_ratio"]
            }

        async def _call() -> dict[str, Any]:
            response = await self._chat._get_client().post(
                f"{self.base_url}/{_model_path(self.model)}:generateContent",
                json=payload,
            )
            response.raise_for_status()
            return dict(response.json())

        data = await with_retry(_call, attempts=self._chat.retry_attempts)
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        for part in parts:
            inline = part.get("inlineData") or {}
            if inline.get("data"):
                return base64.b64decode(inline["data"])
        return None

    async def aclose(self) -> None:
        await self._chat.aclose()


def _json(text: str) -> dict[str, Any]:
    import json

    return dict(json.loads(text))


def gemini_llm(
    api_key: str | None = None,
    model: str = "gemini-2.0-flash",
    base_url: str = _DEFAULT_BASE,
    **kwargs: Any,
) -> GeminiProvider | None:
    """Builds a Gemini chat provider (key from GEMINI_API_KEY).

    Knobs: GEMINI_PROXY / GEMINI_AUTH_HEADER / GEMINI_AUTH_SCHEME.
    """
    import os

    if api_key is None:
        api_key = kwargs.get("api_key") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    merged = {**_network_knobs("GEMINI", kwargs), **kwargs}
    return GeminiProvider(api_key=api_key, model=model, base_url=base_url, **merged)


def gemini_image(
    api_key: str | None = None,
    model: str = "gemini-2.0-flash-exp",
    base_url: str = _DEFAULT_BASE,
    **kwargs: Any,
) -> GeminiImageProvider | None:
    """Builds a Gemini image generator (key from GEMINI_API_KEY)."""
    import os

    if api_key is None:
        api_key = kwargs.get("api_key") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    merged = {**_network_knobs("GEMINI", kwargs), **kwargs}
    return GeminiImageProvider(
        api_key=api_key, model=model, base_url=base_url, **merged
    )
