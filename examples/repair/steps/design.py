"""repair produce — LLM design options (§45, §59).

`design_options` come from the LLM (or the deterministic fallback only when no
model is configured). Preview rendering lives in `services/images.py`: this
module only decides *what* to render and hands the options to the renderer when
`ImageRenderSettings` says it is enabled.
"""

from __future__ import annotations

from reactifact import LLMProvider, Progress
from reactifact.structured import structured_llm

from ..fallbacks import fallback_options
from ..models import DesignOption, DesignOptions, ProjectInfo
from ..services.geometry import geometry_text
from ..services.images import ImageRenderSettings, render_previews
from .common import _list_preferences


async def _make_design_options(
    info: ProjectInfo,
    *,
    llm: LLMProvider | None,
    progress: Progress,
    previews: ImageRenderSettings,
    avoid: list[str] | None = None,
) -> list[DesignOption] | None:
    """Designs from the LLM. If a model is configured but returns no valid options —
    return None (an honest failure), not canned stubs. The deterministic
    fallback remains for demo mode without an LLM only.
    """
    progress("Подбираю варианты дизайна…")
    prefs = _list_preferences(info)
    prefs_text = f"Пожелания пользователя: {prefs}. " if prefs else ""
    avoid_text = (
        "Пользователю не понравились предыдущие варианты: "
        + ", ".join(avoid)
        + ". Предложи другие названия и палитры, не повторяй их. "
        if avoid
        else ""
    )
    result = await structured_llm(
        llm,
        schema=DesignOptions,
        temperature=0.1,
        max_tokens=3000,
        user=(
            "Предложи 3 варианта дизайна комнаты, учитывая пожелания пользователя "
            "(стиль можно варьировать, но заявленные пожелания соблюдай). "
            "Каждый вариант: name, palette (style, wall_color, ceiling_color, "
            "floor_material — словами на русском), description.\n"
            f"{avoid_text}{prefs_text}Геометрия: {geometry_text(info)}"
        ),
    )
    if result is not None and result.options:
        options = result.options
    elif llm is None:
        options = fallback_options(info)
    else:
        return None  # the model gave no options — do not substitute stubs

    if previews.enabled:
        progress("Рендерю фото-превью вариантов…")
        options = await render_previews(previews, info, options, progress)
    return options


__all__ = ["_make_design_options"]
