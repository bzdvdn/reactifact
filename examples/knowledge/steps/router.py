"""knowledge router — greeting/direct reply vs research (§48, §67)."""

from __future__ import annotations

from typing import Annotated

from reactifact import Artifact, Context, Step, When
from reactifact.structured import structured_llm

from ..models import AnswerBody, ChatReply, ResearchTurn, UserQuery
from .common import GREETING_RE, GREETING_TEXT, RESEARCH_RE


class PlannerReply(Step):
    """ChatReply: greeting / direct reply; research goes to PlannerTurn."""

    consumes = (
        Annotated[UserQuery, When(lambda query: not RESEARCH_RE.search(query.text))],
    )
    produces = (ChatReply,)

    async def run(self, context: Context, query: Artifact[UserQuery]) -> ChatReply:
        user = query.data
        text = user.text.strip()
        context.announce("Thinking...", kind="status")

        if GREETING_RE.match(text):
            context.announce("Replying to the greeting", kind="status")
            return ChatReply(query_id=query.id, text=GREETING_TEXT, kind="greeting")

        context.announce("Replying from general knowledge", kind="status")
        answer = await structured_llm(
            context,
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

    async def run(self, context: Context, query: Artifact[UserQuery]) -> ResearchTurn:
        user = query.data
        text = user.text.strip()
        context.announce("Question requires a documentation search", kind="status")
        return ResearchTurn(query_id=query.id, text=text, status="researching")
