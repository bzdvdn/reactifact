"""The template's only domain step: one structured answer per user turn."""

from __future__ import annotations

from reactifact import Artifact, Ensure, LLMProvider, step
from reactifact.structured import structured_llm

from app.agent.prompts import ANSWER_SYSTEM
from app.models import Answer, AnswerBody, UserQuery


@step(produces=Answer, reads=UserQuery)
async def answer(
    query: Artifact[UserQuery],
    llm: LLMProvider | None,
) -> Ensure | None:
    """Turn a non-empty query into one idempotent answer.

    The typed signature is the whole contract: `Artifact[UserQuery]` is the
    step input, `LLMProvider | None` is injected from resources, and the
    returned `Ensure` expresses "create once from this query" without touching
    `Effects` or inventing the answer id by hand. An empty query or an offline
    provider returns `None` — no fake answer artifact.
    """
    if not query.data.text.strip():
        return None
    body = await structured_llm(
        llm,
        schema=AnswerBody,
        system=ANSWER_SYSTEM,
        user=query.data.text,
    )
    if body is None:
        return None
    return Ensure(
        Answer(query_id=query.id, text=body.text),
        source=query,
        prefix="answer",
    )


__all__ = ["answer"]
