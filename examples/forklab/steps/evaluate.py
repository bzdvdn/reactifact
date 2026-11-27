"""forklab — the answer over merged evidence (§34, §67, §68).

Ranking, source selection and the `supported_by` links are *deterministic* —
data and provenance are never the model's job. The model (if configured, §68)
synthesizes the final wording from the branch-tagged findings; without a model,
the top findings are printed verbatim (honest fallback, §59).
"""

from __future__ import annotations

from reactifact import Artifact, Consume, Context, Emit, Step
from reactifact.structured import structured_llm

from ..models import Answer, AnswerBody, Evidence, Question, Review
from ..prompts import synthesis_prompt

TOPN = 3


class Evaluate(Step):
    """Deterministic ranking + links; LLM wording on top of the merged state."""

    consumes = (Review, Consume(Evidence, wakes=False))
    produces = (Answer,)

    async def run(
        self,
        context: Context,
        evidences: list[Artifact[Evidence]],
    ) -> Emit | None:
        if not evidences:
            return None
        ranked = sorted(evidences, key=lambda e: e.data.score, reverse=True)
        top = ranked[:TOPN]

        answer_id = "answer:merged"
        return Emit(
            Answer(
                text=await self._synthesize(context, top, ranked),
                sources=[e.data.source for e in top],
            ),
            id=answer_id,
            links=tuple(("supported_by", evidence) for evidence in ranked),
        )

    async def _synthesize(
        self,
        context: Context,
        top: list[Artifact[Evidence]],
        ranked: list[Artifact[Evidence]],
    ) -> str:
        self.progress("synthesizing the answer over the merged evidence…")
        facts = "\n".join(
            f"- [{evidence.data.branch} / score {evidence.data.score:g}] {evidence.data.text}"
            for evidence in ranked
        )
        if context.resources.llm is None or not top:
            # Deterministic fallback (§59): the strongest findings, verbatim.
            return "\n".join(f"[{e.data.branch}] {e.data.text}" for e in top)
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
        if body is not None:
            return body.text
        return "\n".join(f"[{e.data.branch}] {e.data.text}" for e in top)


__all__ = ["Evaluate"]
