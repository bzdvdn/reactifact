"""research verify — deterministic claim verification (§35, §67)."""

from __future__ import annotations

from reactifact import Produce, ProduceCall

from ..models import Claim, Evidence
from .common import split_sentences, token_support


class VerifyClaims(Produce[Claim]):
    """Deterministic verification (§67): a sentence becomes a claim with
    confidence = how much of it the source page supports (§35, §68)."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        evidence_art = call.trigger
        if evidence_art is None or not isinstance(evidence_art.data, Evidence):
            return None
        evidence = evidence_art.data
        docs = context.related(evidence_art.id, relation="extracted_from")
        doc_text = docs[0].data.content if docs else ""

        context.announce(
            f"Verifying facts from «{evidence.source}»...",
            kind="status",
            source=evidence.source,
        )
        for i, sentence in enumerate(split_sentences(evidence.text)):
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
            claim_id = f"claim:{evidence_art.id}:{i}"
            claim = self.effects.create(
                Claim(
                    query_id=evidence.query_id,
                    text=sentence,
                    confidence=confidence,
                    status=status,
                ),
                id=claim_id,
            )
            claim.link("derived_from", evidence_art)
        return None
