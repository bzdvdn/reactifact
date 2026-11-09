"""repair produce — LLM design options and preview rendering (§45, §59).

`design_options` come from the LLM (or the deterministic fallback only when no
model is configured); previews are rendered strictly one-by-one (OpenRouter
429s on parallel /images), each bounded and retried, failures do not abort the
flow.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from reactifact.context import Context
from reactifact.structured import structured_llm

from ..fallbacks import fallback_options
from ..image_prompt import build_image_prompt
from ..models import DesignOption, DesignOptions, ProjectInfo
from ..services.geometry import geometry_text
from .common import (
    _IMAGE_RENDER_RETRIES,
    _IMAGE_RENDER_TIMEOUT,
    _IMAGE_RETRY_DELAY,
    _list_preferences,
)

logger = logging.getLogger(__name__)


async def _make_design_options(
    context: Context, info: ProjectInfo
) -> list[DesignOption] | None:
    """Designs from the LLM. If a model is configured but returns no valid options —
    return None (an honest failure), not canned stubs. The deterministic
    fallback remains for demo mode without an LLM only.
    """
    context.announce("Подбираю варианты дизайна…", kind="status")
    prefs = _list_preferences(info)
    prefs_text = f"Пожелания пользователя: {prefs}. " if prefs else ""
    result = await structured_llm(
        context,
        schema=DesignOptions,
        temperature=0.1,
        max_tokens=3000,
        user=(
            "Предложи 3 варианта дизайна комнаты, учитывая пожелания пользователя "
            "(стиль можно варьировать, но заявленные пожелания соблюдай). "
            "Каждый вариант: name, palette (style, wall_color, ceiling_color, "
            "floor_material — словами на русском), description.\n"
            f"{prefs_text}Геометрия: {geometry_text(info)}"
        ),
    )
    if result is not None and result.options:
        options = result.options
    elif context.resources.llm is None:
        options = fallback_options(info)
    else:
        return None  # the model gave no options — do not substitute stubs

    images = context.resources.get("images")
    if images is not None:
        context.announce("Рендерю фото-превью вариантов…", kind="status")
        options = await _render_previews(context, images, info, options)
    return options


async def _render_previews(
    context: Context,
    images: Any,
    info: ProjectInfo,
    options: list[DesignOption],
) -> list[DesignOption]:
    """Sequential preview rendering with a timeout and retries.

    OpenRouter /images answers 429 on parallel requests — render strictly one
    by one, pausing between retries to stay within the rate limit. Each call is
    bounded by `asyncio.wait_for`; a single option failing does not break the flow (§59).
    """
    raw_dir = context.resources.get("images_dir")
    if not raw_dir:
        return options
    from pathlib import Path

    out_dir = Path(raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(options)
    timeout: float = context.resources.get("images_timeout") or _IMAGE_RENDER_TIMEOUT
    retries: int = int(context.resources.get("images_retries") or _IMAGE_RENDER_RETRIES)
    retry_delay: float = float(
        context.resources.get("images_retry_delay") or _IMAGE_RETRY_DELAY
    )
    rendered: list[DesignOption] = []
    for index, option in enumerate(options):
        png = None
        for attempt in range(retries):
            try:
                png = await asyncio.wait_for(
                    images.generate(
                        build_image_prompt(info, option),
                        aspect_ratio="1:1",
                        output_format="png",
                    ),
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "preview %s attempt %s failed: %r", index + 1, attempt + 1, exc
                )
                png = None
            if png:
                break
            if attempt + 1 < retries:
                await asyncio.sleep(retry_delay)  # pause against 429
        context.announce(
            (
                f"Превью {index + 1}/{total} готово"
                if png
                else f"Превью {index + 1}/{total}: пропущено"
            ),
            kind="status",
        )
        if png:
            name = f"design-{index}-{uuid.uuid4().hex[:8]}.png"
            (out_dir / name).write_bytes(png)
            rendered.append(
                option.model_copy(update={"preview": f"/assets/generated/{name}"})
            )
        else:
            rendered.append(option)
    return rendered


__all__ = ["_make_design_options", "_render_previews"]
