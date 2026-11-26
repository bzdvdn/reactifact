"""repair services — design preview rendering and its settings.

One typed resource (`ImageRenderSettings`) replaces the loose
`images`/`images_dir`/`images_timeout`/... string keys: register it on
`RuntimeResources`, inject it into the step, and the renderer owns the
one-by-one retry/timeout policy.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator
from reactifact import Progress
from reactifact.providers import ImageProvider

from ..image_prompt import build_image_prompt
from ..models import DesignOption, ProjectInfo

logger = logging.getLogger(__name__)


class ImageRenderSettings(BaseModel):
    """Where and how previews are rendered.

    A pydantic model so env/config values are validated and coerced (`Path`
    from a string, positive `attempts`, …) while the live `ImageProvider`
    stays an arbitrary object. Rendering is opt-in: with no `provider` or no
    `out_dir` it is disabled, so offline demos, scenarios and tests run without
    an image backend. `url_prefix` is the public path the web app mounts
    `out_dir` under.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    provider: ImageProvider | None = None
    out_dir: Path | None = None
    url_prefix: str = "/assets/generated"
    timeout: float = 60.0
    #: Total attempts per option (the first call plus retries).
    attempts: int = 3
    retry_delay: float = 2.0
    aspect_ratio: str = "1:1"
    output_format: str = "png"

    @field_validator("attempts")
    @classmethod
    def _at_least_one_attempt(cls, value: int) -> int:
        if value < 1:
            raise ValueError("ImageRenderSettings.attempts must be at least 1")
        return value

    @property
    def enabled(self) -> bool:
        return self.provider is not None and self.out_dir is not None

    async def aclose(self) -> None:
        """Closes the image provider when this settings object owns one.

        Registered with `resources.register(..., owned=True)`, this runs on
        `RuntimeResources.aclose()` — including the per-turn close that
        `ChatAssistant` performs for a callable `resources=` factory.
        """
        provider = self.provider
        if provider is None:
            return
        closer = getattr(provider, "aclose", None)
        if not callable(closer):
            closer = getattr(provider, "close", None)
        if not callable(closer):
            return
        result = closer()
        if inspect.isawaitable(result):
            await result


async def render_previews(
    settings: ImageRenderSettings,
    info: ProjectInfo,
    options: list[DesignOption],
    progress: Progress,
) -> list[DesignOption]:
    """Sequential preview rendering with a timeout and retries.

    OpenRouter /images answers 429 on parallel requests — render strictly one
    by one, pausing between retries to stay within the rate limit. Each call is
    bounded by `asyncio.wait_for`; a single option failing does not break the
    flow (§59).
    """
    if not settings.enabled:
        return options
    assert settings.provider is not None and settings.out_dir is not None
    provider = settings.provider
    out_dir = settings.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = settings.url_prefix.rstrip("/")
    total = len(options)
    rendered: list[DesignOption] = []
    for index, option in enumerate(options):
        png = None
        for attempt in range(settings.attempts):
            try:
                png = await asyncio.wait_for(
                    provider.generate(
                        build_image_prompt(info, option),
                        aspect_ratio=settings.aspect_ratio,
                        output_format=settings.output_format,
                    ),
                    timeout=settings.timeout,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "preview %s attempt %s failed: %r", index + 1, attempt + 1, exc
                )
                png = None
            if png:
                break
            if attempt + 1 < settings.attempts:
                await asyncio.sleep(settings.retry_delay)  # pause against 429
        message = (
            f"Превью {index + 1}/{total} готово"
            if png
            else f"Превью {index + 1}/{total}: пропущено"
        )
        progress(message)
        if png:
            name = f"design-{index}-{uuid.uuid4().hex[:8]}.{settings.output_format}"
            (out_dir / name).write_bytes(png)
            rendered.append(option.model_copy(update={"preview": f"{prefix}/{name}"}))
        else:
            rendered.append(option)
    return rendered


__all__ = ["ImageRenderSettings", "render_previews"]
