"""design_choice helpers — regenerate the options or handle a style change."""

from __future__ import annotations

from typing import Any

from reactifact import Artifact, Edit, Emit, LLMProvider, Progress
from reactifact.recipes import changed_fields

from ...models import Project, ProjectInfo, UserMsg
from ...services.geometry import ensure_geometry
from ...services.images import ImageRenderSettings
from ..common import _extract_info, _reply
from ..design import _make_design_options


async def _regenerate_options(
    project: Artifact[Project],
    data: Project,
    msg: Artifact[UserMsg],
    *,
    llm: LLMProvider | None,
    progress: Progress,
    previews: ImageRenderSettings,
) -> Any:
    """The human rejected the current options — ask for a fresh set."""
    if llm is None:
        return (
            Edit(project, handled_msg=msg.id),
            Emit(
                _reply(
                    msg.id,
                    "Без модели не могу придумать новые варианты. Выберите один "
                    "из текущих или уточните стиль — тогда обновлю палитру.",
                ),
                id=f"reply:{msg.id}",
            ),
        )
    progress("Подбираю другие варианты…")
    options = await _make_design_options(
        data.info,
        llm=llm,
        progress=progress,
        previews=previews,
        avoid=[option.name for option in data.design_options],
    )
    if options is None:
        return (
            Edit(project, handled_msg=msg.id),
            Emit(
                _reply(
                    msg.id,
                    "Не удалось подобрать другие варианты. Попробуйте ещё раз.",
                ),
                id=f"reply:{msg.id}",
            ),
        )
    previews_text = "\n".join(
        f"{i + 1}. {option.name} — {option.description}"
        for i, option in enumerate(options)
    )
    return (
        Edit(project, design_options=options, handled_msg=msg.id),
        Emit(
            _reply(
                msg.id,
                f"Вот другие варианты:\n{previews_text}",
                images=[option.preview for option in options if option.preview],
            ),
            id=f"reply:{msg.id}",
        ),
    )


async def _handle_style_change(
    project: Artifact[Project],
    msg: Artifact[UserMsg],
    current_info: ProjectInfo,
    *,
    llm: LLMProvider | None,
    progress: Progress,
    previews: ImageRenderSettings,
) -> Any:
    """A message at the choice stage without a number — likely a style/palette change.

    Extract updated facts; if something actually changed — regenerate the
    options (like the rollback "style → design_choice" in REPAIR). Otherwise —
    hint to pick a number.
    """
    text_low = msg.data.text.lower()
    if not any(
        keyword in text_low
        for keyword in (
            "стил",
            "дизайн",
            "палитр",
            "давай",
            "цвет",
            "потолок",
            "стен",
            "пол",
        )
    ):
        return _hint(msg.id)

    new_info = ensure_geometry(
        await _extract_info(llm, progress, msg.data.text, current_info)
    )
    if not changed_fields(current_info, new_info):
        return _hint(msg.id)

    progress("Обновляю варианты под ваш запрос…")
    options = await _make_design_options(
        new_info, llm=llm, progress=progress, previews=previews
    )
    if options is None:
        return Emit(
            _reply(msg.id, "Не удалось обновить варианты. Попробуйте ещё раз."),
            id=f"reply:{msg.id}",
        )
    previews_text = "\n".join(
        f"{i + 1}. {o.name} — {o.description}" for i, o in enumerate(options)
    )
    return (
        Edit(
            project,
            info=new_info,
            design_options=options,
            handled_msg=msg.id,
        ),
        Emit(
            _reply(
                msg.id,
                f"Обновил варианты:\n{previews_text}",
                images=[o.preview for o in options if o.preview],
            ),
            id=f"reply:{msg.id}",
        ),
    )


def _hint(msg_id: str) -> Emit:
    return Emit(
        _reply(msg_id, "Выберите номер варианта, например: 1"),
        id=f"reply:{msg_id}",
    )


__all__ = ["_handle_style_change", "_regenerate_options"]
