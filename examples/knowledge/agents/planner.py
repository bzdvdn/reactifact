"""Router: greeting/direct reply vs research."""

from __future__ import annotations

import logging

from reactifact import Artifact, Consume, LLMProvider, Progress
from reactifact.agent import agent
from reactifact.structured import structured_llm

from ..models import AnswerBody, ChatReply, ResearchTurn, UserQuery
from .common import GREETING_RE, GREETING_TEXT, RESEARCH_RE

logger = logging.getLogger(__name__)


@agent(
    name="planner",
    consumes=[Consume(UserQuery)],
    produces=(ChatReply, ResearchTurn),
)
async def plan(
    query: Artifact[UserQuery],
    progress: Progress,
    llm: LLMProvider | None,
) -> ChatReply | ResearchTurn:
    """One message becomes either a direct reply or a research turn."""
    user = query.data
    text = user.text.strip()
    if RESEARCH_RE.search(text):
        progress("Question requires a documentation search")
        return ResearchTurn(query_id=query.id, text=text, status="researching")

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


__all__ = ["plan"]
