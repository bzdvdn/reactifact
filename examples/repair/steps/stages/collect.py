"""collect stage — extract facts; once complete, propose design options."""

from __future__ import annotations

from typing import Any

from reactifact import Artifact, Context, Edit, Emit, LLMProvider, Progress, Step

from ...models import ChatReply, Project, UserMsg
from ...services.facts import FACT_LABELS, REQUIRED_FACTS
from ...services.fast import fast_reply
from ...services.geometry import ensure_geometry
from ...services.images import ImageRenderSettings
from ..common import _extract_info, _project_artifact, _reply
from ..design import _make_design_options


class CollectStage(Step):
    """Extracts facts; once all the required ones are present — designs and design_choice."""

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
        if project_art is None:
            return Project()
        data = project if project is not None else project_art.data
        if data.stage != "collect":
            return None
        if user.id == data.handled_msg:
            return None
        progress("Думаю…")
        fast = fast_reply(user.data.text)
        if fast is not None:
            return (
                Edit(project_art, handled_msg=user.id),
                Emit(_reply(user.id, fast), id=f"reply:{user.id}"),
            )

        info = ensure_geometry(
            await _extract_info(llm, progress, user.data.text, data.info)
        )
        missing = [f for f in REQUIRED_FACTS if getattr(info, f) is None]
        if missing:
            labeled = ", ".join(FACT_LABELS[f] for f in missing)
            return (
                Edit(project_art, info=info, handled_msg=user.id),
                Emit(
                    _reply(user.id, f"Уточните, пожалуйста: {labeled}."),
                    id=f"reply:{user.id}",
                ),
            )

        options = await _make_design_options(
            info, llm=llm, progress=progress, previews=previews
        )
        if options is None:
            return (
                Edit(project_art, info=info, handled_msg=user.id),
                Emit(
                    _reply(
                        user.id,
                        "Не удалось подобрать варианты дизайна. Попробуйте ещё "
                        "раз или сформулируйте иначе (например, «предложи "
                        "варианты»).",
                    ),
                    id=f"reply:{user.id}",
                ),
            )
        previews_text = "\n".join(
            f"{i + 1}. {o.name} — {o.description}" for i, o in enumerate(options)
        )
        return (
            Edit(
                project_art,
                info=info,
                design_options=options,
                stage="design_choice",
                handled_msg=user.id,
            ),
            Emit(
                _reply(
                    user.id,
                    f"Выберите вариант:\n{previews_text}",
                    images=[o.preview for o in options if o.preview],
                ),
                id=f"reply:{user.id}",
            ),
        )


__all__ = ["CollectStage"]
