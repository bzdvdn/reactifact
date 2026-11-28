"""Evidence extraction and deterministic claim verification."""

from __future__ import annotations

from reactifact import (
    Artifact,
    Consume,
    Context,
    Edit,
    Emit,
    LLMProvider,
    Progress,
    Relate,
)
from reactifact.agent import agent
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


@agent(name="evidence_builder", consumes=[Consume(TypedDoc)], produces=Evidence)
async def extract_evidence(
    doc_artifact: Artifact[TypedDoc],
    progress: Progress,
    llm: LLMProvider | None,
) -> Emit:
    """A key fact from the document (schema → fact, §18)."""
    doc = doc_artifact.data
    progress(f"Extracting key facts from «{doc.path}»...", path=doc.path)
    body = await structured_llm(
        llm,
        schema=AnswerBody,
        user=f"Extract a short factual digest from the document:\n{doc.content}",
    )
    text = (
        body.text.strip()
        if body
        else truncate_at_word(" ".join(doc.content.split()), 200)
    )
    evidence_id = f"evidence:{doc.query_id}:{doc.path}"
    return Emit(
        Evidence(
            query_id=doc.query_id,
            text=text,
            source=f"{doc.source_id}:{doc.path}",
            score=doc.score,
        ),
        id=evidence_id,
        links=(("extracted_from", doc_artifact),),
    )


def _similarity(a: str, b: str) -> float:
    ta, tb = claim_tokens(a), claim_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


@agent(
    name="verifier",
    consumes=[Consume(Evidence)],
    produces=Claim,
    scope="query_id",
)
async def verify_claims(
    context: Context,
    evidence_art: Artifact[Evidence],
    claims: list[Artifact[Claim]],
) -> list[Emit | Relate | Edit]:
    """Builds verifiable claims from a fact and computes their confirmation (§35)."""
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
            if _similarity(a_text, b_text) >= 0.5 and has_negation(
                a_text
            ) != has_negation(b_text):
                conflicting.update({aid, bid})
        for other in claims:
            if _similarity(a_text, other.data.text) >= 0.5 and has_negation(
                a_text
            ) != has_negation(other.data.text):
                conflicting.update({aid, other.id})

    results: list[Emit | Relate | Edit] = []
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
        results.append(
            Emit(
                Claim(
                    query_id=evidence.query_id,
                    text=sentence,
                    confidence=confidence,
                    status=status,
                    conflict=aid in conflicting,
                ),
                id=aid,
                links=(("derived_from", evidence_art),),
            )
        )

    for i, (aid, a_text) in enumerate(new_items):
        for bid, b_text in new_items[i + 1 :]:
            if _similarity(a_text, b_text) >= 0.5 and has_negation(
                a_text
            ) != has_negation(b_text):
                results.append(Relate(aid, "contradicted_by", bid))
                results.append(Relate(bid, "contradicted_by", aid))
    for other in claims:
        if other.id in conflicting and not other.data.conflict:
            results.append(Edit(other, conflict=True))
    return results


__all__ = ["extract_evidence", "verify_claims"]
