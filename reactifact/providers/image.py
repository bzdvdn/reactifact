"""Image generation: OpenRouter (/images), factory from env.

Not part of the core public API — the app wires `ImageProvider`
into resources if needed.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from typing import Any

import httpx

from .._httpx import LoopBoundClient
from ._retry import with_retry
from .chat import _network_knobs
from .contracts import auth_value


class ImageProvider(ABC):
    """Image generation (e.g., OpenRouter /images)."""

    @abstractmethod
    async def generate(self, prompt: str, **params: Any) -> bytes | None:
        """Returns PNG/JPEG bytes or None if generation failed."""
        ...


class OpenAICompatImageProvider(ImageProvider):
    """OpenAI-compatible image generator (OpenAI images, OpenRouter, Azure, ...).

    POSTs to `{base}/images` and decodes the `b64_json` of the first result.
    Auth header/scheme and proxy are configurable like the chat providers.
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        model: str = "gpt-image-1",
        timeout: float = 120.0,
        transport: Any | None = None,
        proxy: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str | None = "Bearer",
        n: int = 1,
        size: str | None = None,
        quality: str | None = None,
        retry_attempts: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.n = n
        self.size = size
        self.quality = quality
        self._timeout = timeout
        self._transport = transport
        self._proxy = proxy
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme
        self.retry_attempts = retry_attempts
        self._http = LoopBoundClient(self._build_client)

    def _build_client(self) -> httpx.AsyncClient:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers[self._auth_header] = auth_value(self.api_key, self._auth_scheme)
        return httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            headers=headers,
            proxy=self._proxy,
        )

    def _get_client(self) -> httpx.AsyncClient:
        return self._http.get()

    async def generate(self, prompt: str, **params: Any) -> bytes | None:
        # Provider-level defaults (n/size/quality) can be overridden per call.
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "n": params.get("n", self.n),
        }
        if "size" in params:
            payload["size"] = params["size"]
        elif self.size is not None:
            payload["size"] = self.size
        if "quality" in params:
            payload["quality"] = params["quality"]
        elif self.quality is not None:
            payload["quality"] = self.quality
        for key in (
            "aspect_ratio",
            "resolution",
            "output_format",
            "style",
            "background",
            "moderation",
        ):
            if key in params:
                payload[key] = params[key]

        async def _generate() -> dict[str, Any]:
            response = await self._get_client().post(
                f"{self.base_url}/images", json=payload
            )
            response.raise_for_status()
            return dict(response.json())

        data = await with_retry(_generate, attempts=self.retry_attempts)
        first = (data.get("data") or [None])[0]
        if not first:
            return None
        if first.get("b64_json"):
            return base64.b64decode(first["b64_json"])
        url = first.get("url")
        if url:

            async def _fetch() -> bytes:
                fetched = await self._get_client().get(url)
                fetched.raise_for_status()
                return fetched.content

            return await with_retry(_fetch, attempts=self.retry_attempts)
        return None

    async def aclose(self) -> None:
        await self._http.aclose()


# Back-compat alias (the previous name, now that the provider is vendor-neutral).
OpenRouterImageProvider = OpenAICompatImageProvider


def image_from_env(**overrides: Any) -> OpenAICompatImageProvider | None:
    """Builds an image generator from IMAGE_* / OPENROUTER_*. Returns
    None if no key is set — the app skips renders. Optional knobs:
    IMAGE_PROXY, IMAGE_AUTH_HEADER, IMAGE_AUTH_SCHEME."""
    import os

    api_key = (
        overrides.get("api_key")
        or os.getenv("IMAGE_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
    )
    if not api_key:
        return None
    merged = {**_network_knobs("IMAGE", overrides), **overrides}
    return OpenAICompatImageProvider(
        base_url=os.getenv("IMAGE_BASE_URL") or "https://openrouter.ai/api/v1",
        api_key=api_key,
        model=os.getenv("IMAGE_MODEL", "google/gemini-3-pro-create-image-plus"),
        **merged,
    )
