"""forklab — the answer over merged evidence (§34, §67, §68).

Ranking, source selection and the `supported_by` links are *deterministic* —
data and provenance are never the model's job. The model (if configured, §68)
synthesizes the final wording from the branch-tagged findings; without a model,
the top findings are printed verbatim (honest fallback, §59).
"""

from __future__ import annotations

from reactifact import Artifact, Context, Produce, ProduceCall
from reactifact.structured import structured_llm

from ..models import Answer, AnswerBody, Evidence, Question
from ..prompts import synthesis_system

TOPN = 3


class Evaluate(Produce[Answer]):
    """Deterministic ranking + links; LLM wording on top of the merged state."""

    artifact_type = Answer

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        evidences = context.list_artifacts(Evidence)
        if not evidences:
            return None
        ranked = sorted(evidences, key=lambda e: e.data.score, reverse=True)
        top = ranked[:TOPN]

        answer_id = "answer:merged"
        answer = self.effects.create(
            Answer(
                text=await self._synthesize(context, top, ranked),
                sources=[e.data.source for e in top],
            ),
            id=answer_id,
        )
        for evidence in ranked:
            answer.link("supported_by", evidence)
        return None

    async def _synthesize(
        self,
        context: Context,
        top: list[Artifact[Evidence]],
        ranked: list[Artifact[Evidence]],
    ) -> str:
        context.announce(
            "synthesizing the answer over the merged evidence…", kind="status"
        )
        facts = "\n".join(
            f"- [{evidence.data.branch} / score {evidence.data.score:g}] {evidence.data.text}"
            for evidence in ranked
        )
        if context.resources.llm is None or not top:
            # Deterministic fallback (§59): the strongest findings, verbatim.
            return "\n".join(f"[{e.data.branch}] {e.data.text}" for e in top)
        questions = context.list_artifacts(Question)
        question = questions[0].data if questions else None
        body = await structured_llm(
            context,
            schema=AnswerBody,
            system=synthesis_system(
                topic=question.topic if question else "unknown",
                question=question.text if question else "unknown",
            ),
            user=(
                f"Findings from competing branches:\n{facts}\nWrite the final answer."
            ),
        )
        if body is not None:
            return body.text
        return "\n".join(f"[{e.data.branch}] {e.data.text}" for e in top)


__all__ = ["Evaluate"]
