"""forklab — one agent that ranks merged evidence and writes the answer."""

from __future__ import annotations

from reactifact import Artifact, Consume, Context, Emit, Progress
from reactifact.agent import agent
from reactifact.structured import structured_llm

from ..models import Answer, AnswerBody, Evidence, Question, Review
from ..prompts import synthesis_prompt

TOPN = 3


@agent(consumes=[Consume(Review)], produces=Answer)
async def evaluate(
    context: Context,
    evidences: list[Artifact[Evidence]],
    progress: Progress,
) -> Emit | None:
    """Deterministic ranking + links; LLM wording on top of the merged state."""
    if not evidences:
        return None
    ranked = sorted(evidences, key=lambda evidence: evidence.data.score, reverse=True)
    top = ranked[:TOPN]

    progress("synthesizing the answer over the merged evidence…")
    facts = "\n".join(
        f"- [{evidence.data.branch} / score {evidence.data.score:g}] {evidence.data.text}"
        for evidence in ranked
    )
    if context.resources.llm is None or not top:
        # Deterministic fallback (§59): the strongest findings, verbatim.
        text = "\n".join(f"[{e.data.branch}] {e.data.text}" for e in top)
    else:
        questions = context.list_artifacts(Question)
        question = questions[0].data if questions else None
        rendered = synthesis_prompt(
            topic=question.topic if question else "unknown",
            question=question.text if question else "unknown",
            facts=facts,
        )
        body = await structured_llm(
            context.resources.llm,
            schema=AnswerBody,
            **rendered.as_kwargs(),
        )
        text = body.text if body is not None else facts
    return Emit(
        Answer(text=text, sources=[e.data.source for e in top]),
        id="answer:merged",
        links=tuple(("supported_by", evidence) for evidence in ranked),
    )


__all__ = ["evaluate"]
