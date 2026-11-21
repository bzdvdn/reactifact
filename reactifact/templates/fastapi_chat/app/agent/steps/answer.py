"""The template's only domain step: one structured answer per user turn."""

from __future__ import annotations

from reactifact import Artifact, Effects, Step
from reactifact.structured import structured_llm

from app.agent.prompts import ANSWER_SYSTEM
from app.models import Answer, AnswerBody, UserQuery


class AnswerStep(Step):
    consumes = (UserQuery,)
    produces = (Answer,)

    async def run(self, query: Artifact[UserQuery], effects: Effects) -> None:
        if not query.data.text.strip():
            return None
        body = await structured_llm(
            self.context,
            schema=AnswerBody,
            system=ANSWER_SYSTEM,
            user=query.data.text,
        )
        if body is None:
            return None
        effects.create_once_from(
            query,
            Answer(query_id=query.id, text=body.text),
            prefix="answer",
        )
        return None


__all__ = ["AnswerStep"]
