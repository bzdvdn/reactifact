"""knowledge router — greeting/direct reply vs research (§48, §67)."""

from __future__ import annotations

from typing import Annotated

from reactifact import Artifact, LLMProvider, Progress, Step, When
from reactifact.structured import structured_llm

from ..models import AnswerBody, ChatReply, ResearchTurn, UserQuery
from .common import GREETING_RE, GREETING_TEXT, RESEARCH_RE


class PlannerReply(Step):
    """ChatReply: greeting / direct reply; research goes to PlannerTurn."""

    consumes = (
        Annotated[UserQuery, When(lambda query: not RESEARCH_RE.search(query.text))],
    )
    produces = (ChatReply,)

    async def run(
        self,
        query: Artifact[UserQuery],
        progress: Progress,
        llm: LLMProvider | None,
    ) -> ChatReply:
        user = query.data
        text = user.text.strip()
        progress("Thinking...")

        if GREETING_RE.match(text):
            progress("Replying to the greeting")
            return ChatReply(query_id=query.id, text=GREETING_TEXT, kind="greeting")

        progress("Replying from general knowledge")
        answer = await structured_llm(
            llm,
            schema=AnswerBody,
            user=f"Answer concisely and to the point:\n{text}",
        )
        reply = (
            answer.text.strip()
            if answer
            else "This question doesn't require consulting the documentation."
        )
        return ChatReply(query_id=query.id, text=reply, kind="direct")


class PlannerTurn(Step):
    """ResearchTurn: for questions that need a source search."""

    consumes = (
        Annotated[UserQuery, When(lambda query: bool(RESEARCH_RE.search(query.text)))],
    )
    produces = (ResearchTurn,)

    async def run(self, query: Artifact[UserQuery], progress: Progress) -> ResearchTurn:
        user = query.data
        text = user.text.strip()
        progress("Question requires a documentation search")
        return ResearchTurn(query_id=query.id, text=text, status="researching")
