"""research lifecycle — deterministic turn overseer and the answer builder."""

from __future__ import annotations

from reactifact import Context, Produce, ProduceCall
from reactifact.recipes import StatusMachine
from reactifact.sources import SourceRef
from reactifact.structured import StructuredLLM

from ..models import (
    Answer,
    AnswerBody,
    Claim,
    Evidence,
    ResearchTurn,
    SearchDone,
)
from .common import FALLBACK_TOPN, turn_of


class EvaluateTurn(StatusMachine[ResearchTurn]):
    """Deterministic turn lifecycle (§24, §69): researching → answerable | insufficient."""

    artifact_type = ResearchTurn
    terminal = frozenset({"answered", "insufficient"})

    def next_status(self, context: Context, key: str) -> str | None:
        refs = [r for r in context.list_artifacts(SourceRef) if r.data.query_id == key]
        evidences = [
            e for e in context.list_artifacts(Evidence) if e.data.query_id == key
        ]
        searched = any(
            s.data.query_id == key for s in context.list_artifacts(SearchDone)
        )
        answered = any(a.data.query_id == key for a in context.list_artifacts(Answer))
        if answered:
            return "answered"
        if refs and evidences:
            return "answerable"
        if searched and not refs:
            return "insufficient"
        return None

    def on_transition(
        self, context: Context, key: str, old_status: str, new_status: str
    ) -> None:
        context.announce(
            f"Research status: {old_status} → {new_status}",
            kind="status",
            query_id=key,
        )


_answer_prompt = StructuredLLM(AnswerBody)


class BuildAnswer(Produce[Answer]):
    """Answer from verified claims, with URL sources (§17, §34)."""

    artifact_type = Answer

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        turn = turn_of(context, call.event)
        if turn is None or turn.status != "answerable":
            return None
        query_id = turn.query_id

        claims = sorted(
            [c for c in context.list_artifacts(Claim) if c.data.query_id == query_id],
            key=lambda c: c.data.confidence,
            reverse=True,
        )
        evidences = [
            e for e in context.list_artifacts(Evidence) if e.data.query_id == query_id
        ]
        if not claims and not evidences:
            return None

        context.announce(
            f"Assembling the answer from {len(claims)} verified claims...",
            kind="status",
            count=len(claims),
        )
        if claims:
            material = "\n".join(
                f"- [conf {c.data.confidence}, {c.data.status}] {c.data.text}"
                for c in claims[:FALLBACK_TOPN]
            )
        else:
            material = "\n".join(f"- {e.data.text}" for e in evidences[:FALLBACK_TOPN])

        body = await _answer_prompt.call(
            context,
            user=f"Answer the question concisely using only the facts.\n"
            f"Question: {turn.text}\nFacts:\n{material}",
        )
        sources = list({e.data.source for e in evidences})
        if body is not None:
            text = body.text.strip()
        else:
            parts = "\n\n".join(
                f"• {c.data.text}" for c in claims[:FALLBACK_TOPN]
            ) or "\n\n".join(f"• {e.data.text}" for e in evidences[:FALLBACK_TOPN])
            text = (
                "No coherent answer was assembled. "
                "Here are the most relevant verified facts:\n\n" + parts
            )
        answer_id = f"answer:{query_id}"
        answer = self.effects.create(
            Answer(query_id=query_id, text=text, sources=sources), id=answer_id
        )
        for evidence_art in evidences:
            answer.link("supported_by", evidence_art)
        return None
