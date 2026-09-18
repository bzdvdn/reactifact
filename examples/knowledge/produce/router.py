"""knowledge router — greeting/direct reply vs research (§48, §67)."""

from __future__ import annotations

from reactifact import Produce, ProduceCall
from reactifact.structured import structured_llm

from ..models import AnswerBody, ChatReply, ResearchTurn
from .common import GREETING_RE, GREETING_TEXT, RESEARCH_RE, user_query


class PlannerReply(Produce[ChatReply]):
    """ChatReply: greeting / direct reply. None for research questions."""

    artifact_type = ChatReply

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        event = call.event
        user = user_query(context, event)
        if user is None or event is None:
            return None
        text = user.text.strip()
        context.announce("Thinking...", kind="status")

        if GREETING_RE.match(text):
            context.announce("Replying to the greeting", kind="status")
            self.effects.create(
                ChatReply(
                    query_id=event.artifact_id, text=GREETING_TEXT, kind="greeting"
                )
            )
            return None

        if RESEARCH_RE.search(text):
            return None  # research branch

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
        self.effects.create(
            ChatReply(query_id=event.artifact_id, text=reply, kind="direct")
        )
        return None


class PlannerTurn(Produce[ResearchTurn]):
    """ResearchTurn: for questions that need a source search."""

    artifact_type = ResearchTurn

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        event = call.event
        user = user_query(context, event)
        if user is None or event is None:
            return None
        text = user.text.strip()
        if not RESEARCH_RE.search(text):
            return None
        context.announce("Question requires a documentation search", kind="status")
        self.effects.create(
            ResearchTurn(query_id=event.artifact_id, text=text, status="researching")
        )
        return None
