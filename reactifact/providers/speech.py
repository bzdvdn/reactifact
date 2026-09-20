"""Speech: text-to-speech (synthesis) and speech-to-text (transcription).

Both are OpenAI-compatible (`/audio/speech` and `/audio/transcriptions`), so
they also cover vendors that mirror OpenAI (Groq Whisper, Azure Speech, ...)
by pointing `base_url` at their OpenAI-compatible endpoint. Auth header/scheme
and proxy are configurable like every other provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import httpx

from .._httpx import LoopBoundClient
from ._retry import with_retry
from .chat import _network_knobs, _resolve_env_api_key
from .contracts import auth_value


class SpeechProvider(ABC):
    """Text-to-speech: returns audio bytes from text."""

    @abstractmethod
    async def synthesize(self, text: str, **params: Any) -> bytes:
        """Synthesizes speech; returns audio bytes (mp3/opus/wav/aac...)."""


class TranscriberProvider(ABC):
    """Speech-to-text: returns a transcript from audio bytes."""

    @abstractmethod
    async def transcribe(self, audio: bytes, **params: Any) -> str:
        """Transcribes audio; returns the text."""


class Connectable:
    """Shared HTTP client builder for the OpenAI-compatible speech endpoints.

    No global Content-Type: httpx sets it per request (JSON for `json=`,
    multipart boundary for `files=`) — forcing it here would break the
    multipart transcription upload.
    """

    def build_client(
        self,
        timeout: float,
        transport: Any,
        proxy: str | None,
        api_key: str | None,
        auth_header: str,
        auth_scheme: str | None,
    ) -> httpx.AsyncClient:
        headers: dict[str, str] = {}
        if api_key:
            headers[auth_header] = auth_value(api_key, auth_scheme)
        return httpx.AsyncClient(
            timeout=timeout, transport=transport, headers=headers, proxy=proxy
        )


class OpenAICompatSpeech(SpeechProvider):
    """TTS via `{base}/audio/speech` (OpenAI tts-1/gpt-4o-mini-tts, Azure...)."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        model: str = "tts-1",
        voice: str = "alloy",
        timeout: float = 60.0,
        transport: Any | None = None,
        proxy: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str | None = "Bearer",
        extra_params: dict[str, Any] | None = None,
        retry_attempts: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self._timeout = timeout
        self._extra = dict(extra_params or {})
        self._transport = transport
        self._proxy = proxy
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme
        self.retry_attempts = retry_attempts
        self._http = LoopBoundClient(self._build_client)

    def _build_client(self) -> httpx.AsyncClient:
        return Connectable().build_client(
            self._timeout,
            self._transport,
            self._proxy,
            self.api_key,
            self._auth_header,
            self._auth_scheme,
        )

    def _get_client(self) -> httpx.AsyncClient:
        return self._http.get()

    async def synthesize(self, text: str, **params: Any) -> bytes:
        payload: dict[str, Any] = {
            "model": params.get("model") or self.model,
            "input": text,
            "voice": params.get("voice") or self.voice,
        }
        for key in ("response_format", "speed", "instructions"):
            if params.get(key):
                payload[key] = params[key]
        payload.update(self._extra)

        async def _call() -> bytes:
            response = await self._get_client().post(
                f"{self.base_url}/audio/speech", json=payload
            )
            response.raise_for_status()
            return response.content

        return await with_retry(_call, attempts=self.retry_attempts)

    async def aclose(self) -> None:
        await self._http.aclose()


class OpenAICompatTranscriber(TranscriberProvider):
    """STT via `{base}/audio/transcriptions` (OpenAI Whisper, Groq Whisper...)."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        model: str = "whisper-1",
        timeout: float = 120.0,
        transport: Any | None = None,
        proxy: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str | None = "Bearer",
        mime_type: str = "audio/webm",
        filename: str = "audio.webm",
        retry_attempts: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._timeout = timeout
        self._transport = transport
        self._proxy = proxy
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme
        self._mime_type = mime_type
        self._filename = filename
        self.retry_attempts = retry_attempts
        self._http = LoopBoundClient(self._build_client)

    def _build_client(self) -> httpx.AsyncClient:
        return Connectable().build_client(
            self._timeout,
            self._transport,
            self._proxy,
            self.api_key,
            self._auth_header,
            self._auth_scheme,
        )

    def _get_client(self) -> httpx.AsyncClient:
        return self._http.get()

    async def transcribe(self, audio: bytes, **params: Any) -> str:
        model = params.get("model") or self.model
        files = {
            "file": (params.get("filename") or self._filename, audio, self._mime_type)
        }
        data: dict[str, str] = {"model": model}
        for key in ("language", "prompt", "response_format"):
            if params.get(key):
                data[key] = str(params[key])

        async def _call() -> str:
            response = await self._get_client().post(
                f"{self.base_url}/audio/transcriptions",
                files=files,
                data=data,
            )
            response.raise_for_status()
            body = response.json()
            if isinstance(body, dict):
                return str(body.get("text", ""))
            return str(body)

        return await with_retry(_call, attempts=self.retry_attempts)

    async def aclose(self) -> None:
        await self._http.aclose()


def _factory_extra(overrides: dict[str, Any], skip: tuple[str, ...]) -> dict[str, Any]:
    return {k: v for k, v in overrides.items() if k not in skip}


def _openai_compat_speech(
    *,
    env_prefix: str,
    default_model: str,
    default_voice: str,
    default_base_url: str,
    env_api_key_vars: tuple[str, ...] | None = None,
    name: str | None = None,
    doc: str = "",
) -> Callable[..., OpenAICompatSpeech]:
    """Builds a `<vendor>_speech(model=..., voice=..., base_url=...,
    api_key=None, **kwargs)` TTS factory for a vendor whose `/audio/speech`
    endpoint is OpenAI-compatible — the speech counterpart of
    `_openai_compat_llm` (`reactifact.providers.chat`).
    """
    key_vars = env_api_key_vars or (f"{env_prefix}_API_KEY",)

    def factory(
        model: str = default_model,
        voice: str = default_voice,
        base_url: str = default_base_url,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> OpenAICompatSpeech:
        merged = {**_network_knobs(env_prefix, kwargs), **kwargs}
        return OpenAICompatSpeech(
            base_url=base_url,
            api_key=_resolve_env_api_key(api_key, key_vars),
            model=model,
            voice=voice,
            **merged,
        )

    factory.__name__ = name or f"{env_prefix.lower()}_speech"
    factory.__qualname__ = factory.__name__
    factory.__doc__ = doc or (
        f"{env_prefix.title()} — OpenAI-compatible text-to-speech "
        f"(key from {' or '.join(key_vars)})."
    )
    return factory


def _openai_compat_transcriber(
    *,
    env_prefix: str,
    default_model: str,
    default_base_url: str,
    env_api_key_vars: tuple[str, ...] | None = None,
    name: str | None = None,
    doc: str = "",
) -> Callable[..., OpenAICompatTranscriber]:
    """Builds a `<vendor>_transcriber(model=..., base_url=..., api_key=None,
    **kwargs)` STT factory for a vendor whose `/audio/transcriptions`
    endpoint takes the same multipart-file request OpenAI's Whisper API
    does (not every "OpenAI-compatible" STT endpoint does — OpenRouter's,
    for one, takes base64 JSON instead, so it does *not* use this factory).
    """
    key_vars = env_api_key_vars or (f"{env_prefix}_API_KEY",)

    def factory(
        model: str = default_model,
        base_url: str = default_base_url,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> OpenAICompatTranscriber:
        merged = {**_network_knobs(env_prefix, kwargs), **kwargs}
        return OpenAICompatTranscriber(
            base_url=base_url,
            api_key=_resolve_env_api_key(api_key, key_vars),
            model=model,
            **merged,
        )

    factory.__name__ = name or f"{env_prefix.lower()}_transcriber"
    factory.__qualname__ = factory.__name__
    factory.__doc__ = doc or (
        f"{env_prefix.title()} — OpenAI-compatible transcription "
        f"(key from {' or '.join(key_vars)})."
    )
    return factory


def speech_from_env(**overrides: Any) -> OpenAICompatSpeech | None:
    """Builds a TTS provider from env (SPEECH_* or OPENAI_*).

    Keys: SPEECH_BASE_URL / SPEECH_API_KEY / SPEECH_MODEL / SPEECH_VOICE plus
    the usual SPEECH_PROXY / SPEECH_AUTH_HEADER / SPEECH_AUTH_SCHEME.
    """
    import os

    api_key = (
        overrides.get("api_key")
        or os.getenv("SPEECH_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    if not api_key:
        return None
    merged = {
        **_network_knobs("SPEECH", overrides),
        **_factory_extra(overrides, ("api_key", "base_url", "model", "voice")),
    }
    return OpenAICompatSpeech(
        base_url=overrides.get("base_url")
        or os.getenv("SPEECH_BASE_URL")
        or "https://api.openai.com/v1",
        api_key=api_key,
        model=overrides.get("model") or os.getenv("SPEECH_MODEL") or "tts-1",
        voice=overrides.get("voice") or os.getenv("SPEECH_VOICE") or "alloy",
        **merged,
    )


def transcriber_from_env(**overrides: Any) -> OpenAICompatTranscriber | None:
    """Builds an STT provider from env (TRANSCRIBER_* or OPENAI_*).

    Keys: TRANSCRIBER_BASE_URL / TRANSCRIBER_API_KEY / TRANSCRIBER_MODEL plus
    the usual TRANSCRIBER_PROXY / TRANSCRIBER_AUTH_HEADER / AUTH_SCHEME.
    Point TRANSCRIBER_BASE_URL at api.groq.com/openai/v1 and set
    TRANSCRIBER_MODEL=whisper-large-v3-turbo for Groq Whisper.
    """
    import os

    api_key = (
        overrides.get("api_key")
        or os.getenv("TRANSCRIBER_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    if not api_key:
        return None
    merged = {
        **_network_knobs("TRANSCRIBER", overrides),
        **_factory_extra(overrides, ("api_key", "base_url", "model")),
    }
    return OpenAICompatTranscriber(
        base_url=overrides.get("base_url")
        or os.getenv("TRANSCRIBER_BASE_URL")
        or "https://api.openai.com/v1",
        api_key=api_key,
        model=overrides.get("model") or os.getenv("TRANSCRIBER_MODEL") or "whisper-1",
        **merged,
    )
