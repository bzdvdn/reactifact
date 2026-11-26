"""assistant stage — open-ended dialogue after approval."""

from __future__ import annotations

from typing import Any

from reactifact import Artifact, Context, Edit, Emit, LLMProvider, Progress, Step
from reactifact.structured import llm_reply

from ...models import ChatReply, Project, UserMsg
from ..common import _assistant_context, _conversation_text, _reply


class AssistantStage(Step):
    """Open-ended dialogue after approval."""

    consumes = (UserMsg,)
    produces = (Project, ChatReply)

    async def run(
        self,
        context: Context,
        user: Artifact[UserMsg],
        project: Artifact[Project] | None,
        progress: Progress,
        llm: LLMProvider | None,
    ) -> Any:
        if project is None or project.data.stage != "assistant":
            return None
        data = project.data
        if user.id == data.handled_msg:
            return None
        progress("Думаю…")
        parts = [
            p
            for p in (
                _assistant_context(data),
                _conversation_text(context, user.id),
            )
            if p
        ]
        context_text = "\n\n".join(parts)
        prompt = (
            f"{context_text}\n\nВопрос: {user.data.text}"
            if context_text
            else user.data.text
        )
        text = await llm_reply(
            llm,
            system=(
                "You are the repair assistant. The project is approved. Answer "
                "briefly and actionably in Russian, based strictly on the plan "
                "and the estimate; do not invent prices that are not in the "
                "estimate."
            ),
            user=prompt,
            attempts=3,
        )
        if not text:
            # Honest fallback (§59): the model was unavailable or its reply did
            # not parse — still answer from the plan, not a canned placeholder.
            text = _assistant_fallback(data)
        return (
            Emit(_reply(user.id, text), id=f"reply:{user.id}"),
            Edit(project, handled_msg=user.id),
        )


def _assistant_fallback(project: Project) -> str:
    """Deterministic answer from the approved plan (no model needed)."""
    if project.plan:
        first = project.plan[0]
        return (
            f"Комната: {project.info.room_type or '—'}. Начните с этапа "
            f"«{first.name}»: {first.description}. Могу отвечать по этапам, "
            f"материалам и ценам из утверждённой сметы."
        )
    return (
        "План и смета утверждены. Спросите про этапы работ, материалы или "
        "цены из сметы — отвечу по плану."
    )


__all__ = ["AssistantStage"]
