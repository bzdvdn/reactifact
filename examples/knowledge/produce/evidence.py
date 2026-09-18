"""knowledge evidence — extraction and deterministic claim verification."""

from __future__ import annotations

from reactifact import Produce, ProduceCall
from reactifact.structured import structured_llm

from ..models import AnswerBody, Claim, Evidence, TypedDoc
from .common import (
    claim_tokens,
    has_negation,
    source_doc_of,
    split_sentences,
    token_support,
    truncate_at_word,
)


class ExtractEvidence(Produce[Evidence]):
    """A key fact from the document (schema → fact, §18)."""

    artifact_type = Evidence

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        doc_artifact = call.trigger
        if doc_artifact is None or not isinstance(doc_artifact.data, TypedDoc):
            return None
        doc = doc_artifact.data
        context.announce(
            f"Extracting key facts from «{doc.path}»...",
            kind="status",
            path=doc.path,
        )
        body = await structured_llm(
            context,
            schema=AnswerBody,
            user=f"Extract a short factual digest from the document:\n{doc.content}",
        )
        text = (
            body.text.strip()
            if body
            else truncate_at_word(" ".join(doc.content.split()), 200)
        )
        evidence_id = f"evidence:{doc.query_id}:{doc.path}"
        # provenance (§34): Evidence —extracted_from→ TypedDoc
        evidence = self.effects.create(
            Evidence(
                query_id=doc.query_id,
                text=text,
                source=f"{doc.source_id}:{doc.path}",
                score=doc.score,
            ),
            id=evidence_id,
        )
        evidence.link("extracted_from", doc_artifact)
        return None


class VerifyClaims(Produce[Claim]):
    """Builds verifiable claims from a fact and computes their confirmation (§35).

    Each Evidence sentence → Claim with confidence (by matching against the
    source-document text). Pairs of claims with high similarity and differing
    polarity get a `contradicted_by` link — a contradiction stays a first-class
    state rather than something hidden in a string (§36, §69).
    """

    artifact_type = Claim

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        ta, tb = claim_tokens(a), claim_tokens(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / min(len(ta), len(tb))

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        evidence_art = call.trigger
        if evidence_art is None or not isinstance(evidence_art.data, Evidence):
            return None
        evidence = evidence_art.data
        doc = source_doc_of(context, evidence_art)
        doc_text = doc.data.content if doc is not None else ""

        context.announce(
            f"Verifying facts from «{evidence.source}»...",
            kind="status",
            source=evidence.source,
        )

        new_items: list[tuple[str, str]] = [
            (f"claim:{evidence_art.id}:{i}", sentence)
            for i, sentence in enumerate(split_sentences(evidence.text))
        ]
        conflicting: set[str] = set()
        for aid, a_text in new_items:
            for bid, b_text in new_items:
                if aid >= bid:
                    continue
                if self._similarity(a_text, b_text) >= 0.5 and has_negation(
                    a_text
                ) != has_negation(b_text):
                    conflicting.update({aid, bid})
            for other in context.list_artifacts(Claim):
                if other.data.query_id != evidence.query_id:
                    continue
                if self._similarity(a_text, other.data.text) >= 0.5 and has_negation(
                    a_text
                ) != has_negation(other.data.text):
                    conflicting.update({aid, other.id})

        for aid, sentence in new_items:
            support = token_support(sentence, doc_text)
            confidence = round(
                min(1.0, (0.3 + 0.7 * support) * 0.6 + 0.4 * (evidence.score or 0.3)),
                2,
            )
            status = (
                "verified"
                if support >= 0.6
                else ("weak" if support >= 0.35 else "unverified")
            )
            claim = self.effects.create(
                Claim(
                    query_id=evidence.query_id,
                    text=sentence,
                    confidence=confidence,
                    status=status,
                    conflict=aid in conflicting,
                ),
                id=aid,
            )
            claim.link("derived_from", evidence_art)

        for i, (aid, a_text) in enumerate(new_items):
            for bid, b_text in new_items[i + 1 :]:
                if self._similarity(a_text, b_text) >= 0.5 and has_negation(
                    a_text
                ) != has_negation(b_text):
                    self.effects.link(aid, "contradicted_by", bid)
                    self.effects.link(bid, "contradicted_by", aid)
        for other in context.list_artifacts(Claim):
            if (
                other.id in conflicting
                and other.data.query_id == evidence.query_id
                and not other.data.conflict
            ):
                self.effects.update(other, conflict=True)
        return None
