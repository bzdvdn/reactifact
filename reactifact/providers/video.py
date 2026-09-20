"""Video generation providers (submit → poll → download).

Video backends (Sora, Runway, ...) expose an async, long-running task API:
you submit a prompt, get a task id, poll until the job completes, then fetch
the finished mp4. The base `_HttpVideoProvider` keeps that contract uniform
and testable (inject `transport` like the chat/image providers).
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from .._httpx import LoopBoundClient
from ._retry import with_retry
from .chat import _network_knobs
from .contracts import auth_value

STATUS_MAP = {
    "queued": "pending",
    "queued_processing": "processing",
    "in_progress": "processing",
    "processing": "processing",
    "running": "processing",
    "dreaming": "processing",
    "completed": "completed",
    "succeeded": "completed",
    "success": "completed",
    "failed": "failed",
    "error": "failed",
}


@dataclass
class VideoResult:
    """State of a video generation task."""

    id: str
    status: str = "pending"  # pending | processing | completed | failed
    url: str | None = None
    error: str | None = None
    data: bytes | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class VideoProvider(ABC):
    """Async video generation: submit → poll → download.

    Subclasses that can fetch the finished file from `result.url` override
    `download` with an HTTP client; the embedded-`data` shortcut lives here.
    """

    @abstractmethod
    async def generate(self, prompt: str, **params: Any) -> str:
        """Submits a generation and returns the task id (str)."""

    @abstractmethod
    async def fetch(self, task_id: str) -> VideoResult:
        """Current state of the task (pending/processing/completed/failed)."""

    async def poll(
        self, task_id: str, timeout: float = 600.0, interval: float = 5.0
    ) -> VideoResult:
        """Polls until completed/failed or the timeout elapses (best effort).

        A `fetch()` that still raises after its own internal retry (a longer
        outage, not a single blip) does not abort the poll — a several-
        minute video job in progress is not worth abandoning over a
        transient network problem, so the loop just waits for the next
        interval and tries again. Only running out of `timeout` while every
        recent fetch failed gives up, with an honest `status="failed"`
        result rather than raising out of a best-effort poll.
        """
        deadline = time.monotonic() + timeout
        last_result: VideoResult | None = None
        last_error: str | None = None
        while True:
            try:
                last_result = await self.fetch(task_id)
                last_error = None
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_error = str(exc)
            else:
                if last_result.status in ("completed", "failed"):
                    return last_result
            if time.monotonic() >= deadline:
                if last_result is not None and last_error is None:
                    return last_result
                return VideoResult(
                    id=task_id,
                    status="failed",
                    error=f"polling timed out after repeated fetch failures: {last_error}",
                )
            await asyncio.sleep(interval)

    async def download(self, result: VideoResult) -> bytes | None:
        """Returns the finished video bytes, or None (no embed, no fetch)."""
        return result.data

    async def aclose(self) -> None:
        http = getattr(self, "_http", None)
        if http is not None:
            await http.aclose()


class _HttpVideoProvider(VideoProvider):
    """HTTP base for video providers: shared client + auth/proxy knobs."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 300.0,
        transport: Any | None = None,
        proxy: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str | None = "Bearer",
        extra_headers: dict[str, str] | None = None,
        retry_attempts: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._timeout = timeout
        self._headers = dict(extra_headers or {"Content-Type": "application/json"})
        if api_key:
            self._headers.setdefault(auth_header, auth_value(api_key, auth_scheme))
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

    async def download(self, result: VideoResult) -> bytes | None:
        if result.data is not None:
            return result.data
        if not result.url:
            return None
        url = result.url

        async def _call() -> bytes:
            response = await self._get_client().get(url)
            response.raise_for_status()
            return response.content

        return await with_retry(_call, attempts=self.retry_attempts)


class SoraVideoProvider(_HttpVideoProvider):
    """OpenAI Sora — `POST /videos` (submit) and `GET /videos/{id}` (poll)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "sora-1",
        base_url: str = "https://api.openai.com/v1",
        **kwargs: Any,
    ):
        super().__init__(base_url=base_url, api_key=api_key, model=model, **kwargs)

    async def generate(self, prompt: str, **params: Any) -> str:
        payload: dict[str, Any] = {"model": self.model, "prompt": prompt}
        for key in ("size", "duration", "quality"):
            if params.get(key):
                payload[key] = params[key]

        async def _call() -> str:
            response = await self._get_client().post(
                f"{self.base_url}/videos", json=payload
            )
            response.raise_for_status()
            return str(response.json()["id"])

        return await with_retry(_call, attempts=self.retry_attempts)

    async def fetch(self, task_id: str) -> VideoResult:
        async def _call() -> VideoResult:
            response = await self._get_client().get(f"{self.base_url}/videos/{task_id}")
            response.raise_for_status()
            data = response.json()
            out = data.get("output") or {}
            return VideoResult(
                id=task_id,
                status=STATUS_MAP.get(
                    str(data.get("status", "")).lower(), "processing"
                ),
                url=out.get("url"),
                error=out.get("error") or data.get("error"),
                extra=data,
            )

        return await with_retry(_call, attempts=self.retry_attempts)


class RunwayVideoProvider(_HttpVideoProvider):
    """Runway ML — `POST /v1/videos` (submit) and `GET /v1/videos/{id}` (poll)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gen3a_turbo",
        base_url: str = "https://api.dev.runwayml.com/v1",
        **kwargs: Any,
    ):
        super().__init__(base_url=base_url, api_key=api_key, model=model, **kwargs)

    async def generate(self, prompt: str, **params: Any) -> str:
        payload: dict[str, Any] = {"model": self.model, "promptText": prompt}
        if params.get("image"):
            payload["promptImage"] = params["image"]
        if params.get("ratio"):
            payload["ratio"] = params["ratio"]

        async def _call() -> str:
            response = await self._get_client().post(
                f"{self.base_url}/v1/videos", json=payload
            )
            response.raise_for_status()
            return str(response.json()["id"])

        return await with_retry(_call, attempts=self.retry_attempts)

    async def fetch(self, task_id: str) -> VideoResult:
        async def _call() -> VideoResult:
            response = await self._get_client().get(
                f"{self.base_url}/v1/videos/{task_id}"
            )
            response.raise_for_status()
            data = response.json()
            output = data.get("output") or {}
            return VideoResult(
                id=task_id,
                status=STATUS_MAP.get(
                    str(data.get("status", "")).lower(), "processing"
                ),
                url=output.get("url"),
                error=output.get("error"),
                extra=data,
            )

        return await with_retry(_call, attempts=self.retry_attempts)


class LumaVideoProvider(_HttpVideoProvider):
    """Luma Dream Machine — `POST /v1/generations` (submit) + GET by id (poll)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "ray-2",
        base_url: str = "https://api.lumalabs.ai/dream-machine/v1",
        **kwargs: Any,
    ):
        super().__init__(base_url=base_url, api_key=api_key, model=model, **kwargs)

    async def generate(self, prompt: str, **params: Any) -> str:
        payload: dict[str, Any] = {"model": self.model, "prompt": prompt}
        if params.get("image"):
            payload["promptImage"] = params["image"]
        if params.get("duration"):
            payload["duration"] = params["duration"]

        async def _call() -> str:
            response = await self._get_client().post(
                f"{self.base_url}/generations", json=payload
            )
            response.raise_for_status()
            return str(response.json()["id"])

        return await with_retry(_call, attempts=self.retry_attempts)

    async def fetch(self, task_id: str) -> VideoResult:
        async def _call() -> VideoResult:
            response = await self._get_client().get(
                f"{self.base_url}/generations/{task_id}"
            )
            response.raise_for_status()
            data = response.json()
            assets = data.get("assets") or {}
            return VideoResult(
                id=task_id,
                status=STATUS_MAP.get(str(data.get("state", "")).lower(), "processing"),
                url=assets.get("video"),
                error=data.get("failure_reason"),
                extra=data,
            )

        return await with_retry(_call, attempts=self.retry_attempts)


class OpenRouterVideoProvider(_HttpVideoProvider):
    """OpenRouter video — Generations API (`POST /api/v1/generations` + poll).

    Same key as chat/images: `OPENROUTER_API_KEY`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "openai/sora-2",
        base_url: str = "https://openrouter.ai/api/v1",
        **kwargs: Any,
    ):
        super().__init__(base_url=base_url, api_key=api_key, model=model, **kwargs)

    async def generate(self, prompt: str, **params: Any) -> str:
        payload: dict[str, Any] = {"model": self.model, "prompt": prompt}
        for key in ("negative_prompt", "height", "width", "guidance_scale"):
            if params.get(key):
                payload[key] = params[key]

        async def _call() -> str:
            response = await self._get_client().post(
                f"{self.base_url}/generations", json=payload
            )
            response.raise_for_status()
            return str(response.json()["id"])

        return await with_retry(_call, attempts=self.retry_attempts)

    async def fetch(self, task_id: str) -> VideoResult:
        async def _call() -> VideoResult:
            response = await self._get_client().get(
                f"{self.base_url}/generations/{task_id}"
            )
            response.raise_for_status()
            data = response.json()
            out = data.get("out") or []
            if out:
                first = out[0]
                url = first.get("video_url") or first.get("url")
                return VideoResult(id=task_id, status="completed", url=url, extra=data)
            if data.get("error"):
                return VideoResult(
                    id=task_id, status="failed", error=str(data["error"])
                )
            return VideoResult(id=task_id, status="processing", extra=data)

        return await with_retry(_call, attempts=self.retry_attempts)


def video_from_env(**overrides: Any) -> VideoProvider | None:
    """Builds a video provider from env (VIDEO_PROVIDER=sora|runway|luma|openrouter).

    Keys: VIDEO_API_KEY (or SORA_API_KEY / RUNWAY_API_KEY / LUMA_API_KEY /
    OPENROUTER_API_KEY / OPENAI_API_KEY), VIDEO_BASE_URL, VIDEO_MODEL, plus the
    usual VIDEO_PROXY / VIDEO_AUTH_HEADER / VIDEO_AUTH_SCHEME knobs.
    """
    import os

    provider_name = str(
        overrides.get("provider") or os.getenv("VIDEO_PROVIDER") or "sora"
    ).lower()
    api_key = (
        overrides.get("api_key")
        or os.getenv("VIDEO_API_KEY")
        or os.getenv(f"{provider_name.upper()}_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    if not api_key:
        return None
    model = overrides.get("model") or os.getenv("VIDEO_MODEL")
    base_url = overrides.get("base_url") or os.getenv("VIDEO_BASE_URL")
    merged = {**_network_knobs("VIDEO", overrides), **overrides}
    if provider_name == "runway":
        return RunwayVideoProvider(
            api_key=api_key,
            model=model or "gen3a_turbo",
            base_url=base_url or "https://api.dev.runwayml.com/v1",
            **merged,
        )
    if provider_name == "luma":
        return LumaVideoProvider(
            api_key=api_key,
            model=model or "ray-2",
            base_url=base_url or "https://api.lumalabs.ai/dream-machine/v1",
            **merged,
        )
    if provider_name == "openrouter":
        return OpenRouterVideoProvider(
            api_key=api_key,
            model=model or "openai/sora-2",
            base_url=base_url or "https://openrouter.ai/api/v1",
            **merged,
        )
    return SoraVideoProvider(
        api_key=api_key,
        model=model or "sora-1",
        base_url=base_url or "https://api.openai.com/v1",
        **merged,
    )
