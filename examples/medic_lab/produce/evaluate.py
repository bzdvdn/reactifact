"""medic-lab evaluation: deterministic scoring and verdicts for hypotheses.

The lexicons decide the numbers, the model never does (§67, §68).
"""

from __future__ import annotations

from reactifact import Artifact, Context, Produce, ProduceCall
from reactifact.sources import SourceRef

from ..models import (
    Claim,
    Evidence,
    Hypothesis,
    Question,
    SearchDone,
    TypedDoc,
)
from .common import hypotheses_of, question_id_of

_SUPPORT_W = 1.0
_CONTRA_W = 1.2
_COVERAGE_W = 0.3
_CONF_W = 0.5
_SUPPORTED_TH = 0.35
_REFUTED_TH = -0.35


def channel_done(context: Context, hyp_art: Artifact[Hypothesis], depth: int) -> bool:
    """A hypothesis round is evaluable only when every fetched page has been
    materialized into evidence and claims.

    Evaluation must not run on «docs exist but facts are still being extracted»
    (slow providers): otherwise a hypothesis would be finalized as
    inconclusive-0.0 before its real supports/contradicts are known.
    """
    marker = f"searched:{hyp_art.id}:{depth}"
    if not any(a.id == marker for a in context.list_artifacts(SearchDone)):
        return False
    refs = [
        r
        for r in context.list_artifacts(SourceRef)
        if r.data.metadata.get("hypothesis_id") == hyp_art.id
        and r.data.metadata.get("round") == depth
    ]
    if not refs:
        return True  # an empty search round is complete, not broken
    docs = [
        d
        for d in context.list_artifacts(TypedDoc)
        if d.data.hypothesis_id == hyp_art.id and d.data.round == depth
    ]
    if not docs:
        return False
    for doc in docs:
        relations = context.incoming(doc.id, relation="extracted_from")
        if not relations:
            return False
        for rel in relations:
            if not context.incoming(rel.source_id, relation="derived_from"):
                return False
    return True


def evaluate_hypothesis(
    context: Context, hyp_art: Artifact[Hypothesis]
) -> dict[str, object] | None:
    """One hypothesis: counts, score, verdict — or None if nothing changed."""
    supports = len(context.incoming(hyp_art.id, relation="supports"))
    contradicts = len(context.incoming(hyp_art.id, relation="contradicts"))
    evidences = [
        e
        for e in context.list_artifacts(Evidence)
        if e.data.hypothesis_id == hyp_art.id
    ]
    coverage = len({e.data.source for e in evidences})
    claims = [
        c for c in context.list_artifacts(Claim) if c.data.hypothesis_id == hyp_art.id
    ]
    confidence = (
        round(sum(c.data.confidence for c in claims) / len(claims), 2)
        if claims
        else 0.0
    )
    score = round(
        _SUPPORT_W * supports
        - _CONTRA_W * contradicts
        + _COVERAGE_W * coverage
        + _CONF_W * confidence,
        2,
    )
    verdict = (
        "supported"
        if score >= _SUPPORTED_TH
        else ("refuted" if score <= _REFUTED_TH else "inconclusive")
    )
    current = hyp_art.data
    if (
        current.status == verdict
        and current.score == score
        and current.supports == supports
        and current.contradicts == contradicts
        and current.coverage == coverage
    ):
        return None
    return {
        "status": verdict,
        "score": score,
        "supports": supports,
        "contradicts": contradicts,
        "coverage": coverage,
        "confidence": confidence,
    }


class Evaluator(Produce[Hypothesis]):
    """Deterministic scorer; re-runs as each channel round materializes."""

    artifact_type = Hypothesis

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        question_id = question_id_of(context, call.event)
        if question_id is None:
            return None
        question = context.get(question_id)
        if question is None or not isinstance(question.data, Question):
            return None
        depth = question.data.depth
        for hyp_art in hypotheses_of(context, question_id):
            if channel_done(context, hyp_art, depth):
                fields = evaluate_hypothesis(context, hyp_art)
                if fields is not None:
                    self.effects.update(hyp_art, **fields)
        return None
