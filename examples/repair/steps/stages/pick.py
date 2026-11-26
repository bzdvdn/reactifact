"""design_choice stage — parse the pick, or regenerate the options."""

from __future__ import annotations

from typing import Any

from reactifact import Artifact, Context, Edit, Emit, LLMProvider, Progress, Step
from reactifact.recipes import changed_fields

from ...models import ChatReply, Project, ProjectInfo, UserMsg
from ...services.geometry import ensure_geometry
from ...services.images import ImageRenderSettings
from ..common import (
    _extract_info,
    _parse_pick,
    _project_artifact,
    _reply,
    _wants_more_options,
)
from ..design import _make_design_options


class PickStage(Step):
    """Parses the user's choice → plan."""

    consumes = (UserMsg, Project)
    produces = (Project, ChatReply)

    async def run(
        self,
        context: Context,
        user: Artifact[UserMsg],
        project: Project | None,
        progress: Progress,
        llm: LLMProvider | None,
        previews: ImageRenderSettings,
    ) -> Any:
        project_art = _project_artifact(context)
        if project_art is None or project_art.data.stage != "design_choice":
            return None
        data = project if project is not None else project_art.data
        if user.id == data.handled_msg:
            return None
        progress("Думаю…")
        text = user.data.text.strip()
        pick = _parse_pick(text, data.design_options)
        if pick is None:
            if _wants_more_options(text):
                return await _regenerate_options(
                    project_art,
                    data,
                    user,
                    llm=llm,
                    progress=progress,
                    previews=previews,
                )
            return await _handle_style_change(
                project_art,
                user,
                data.info,
                llm=llm,
                progress=progress,
                previews=previews,
            )
        return (
            Edit(
                project_art,
                design_choice=pick.name,
                palette=pick.palette,
                stage="plan",
                handled_msg=user.id,
                plan=[],
                estimate=None,
            ),
            Emit(
                _reply(user.id, f"Отлично, «{pick.name}». Составляю план…"),
                id=f"reply:{user.id}",
            ),
        )


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


__all__ = ["PickStage"]
