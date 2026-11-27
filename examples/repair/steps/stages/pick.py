"""design_choice stage — parse the pick, or regenerate the options."""

from __future__ import annotations

from typing import Any

from reactifact import Artifact, Edit, Emit, LLMProvider, Progress, Step

from ...models import ChatReply, Project, UserMsg
from ...services.images import ImageRenderSettings
from ..common import _parse_pick, _reply, _wants_more_options
from .redesign import _handle_style_change, _regenerate_options


class PickStage(Step):
    """Parses the user's choice → plan."""

    reads = (UserMsg,)
    produces = (Project, ChatReply)

    async def run(
        self,
        user: Artifact[UserMsg],
        project: Artifact[Project] | None,
        progress: Progress,
        llm: LLMProvider | None,
        previews: ImageRenderSettings,
    ) -> Any:
        if project is None or project.data.stage != "design_choice":
            return None
        data = project.data
        if user.id == data.handled_msg:
            return None
        progress("Думаю…")
        text = user.data.text.strip()
        pick = _parse_pick(text, data.design_options)
        if pick is None:
            if _wants_more_options(text):
                return await _regenerate_options(
                    project,
                    data,
                    user,
                    llm=llm,
                    progress=progress,
                    previews=previews,
                )
            return await _handle_style_change(
                project,
                user,
                data.info,
                llm=llm,
                progress=progress,
                previews=previews,
            )
        return (
            Edit(
                project,
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


__all__ = ["PickStage"]
