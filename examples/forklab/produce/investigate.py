"""forklab — deterministic investigation strategies with LLM wording (§24, §67).

Each strategy picks its documents and scores them *deterministically* (no LLM —
the branching, scoring and provenance are the demonstration). The model is used
only where generation is genuinely needed: wording each finding in natural
language. If no model is configured, the snippet text is used as-is (honest
fallback, §59) — the pipeline still runs fully offline.
"""

from __future__ import annotations

from reactifact import Artifact, Context, Produce, ProduceCall
from reactifact.structured import structured_llm

from ..models import Evidence, EvidenceBody, Question, Strategy
from ..prompts import wording_system


async def _wording(context: Context, snippet: str) -> str:
    """LLM phrasing of a finding; the deterministic snippet on no-model/parse-fail.

    The model always knows the *topic* and the *question* it is wording for
    (from the live `Question` artifact) — it never works without domain context (§68).
    """
    if context.resources.llm is None:
        return snippet
    questions = context.list_artifacts(Question)
    question = questions[0].data if questions else None
    body = await structured_llm(
        context,
        schema=EvidenceBody,
        system=wording_system(
            topic=question.topic if question else "unknown",
            question=question.text if question else "unknown",
        ),
        user=f"Document snippet: {snippet}",
    )
    return body.text if body is not None else snippet


#: An inline document catalog: (source_id, snippet, quality).
_CATALOG: list[tuple[str, str, float]] = [
    (
        "doc:overview",
        "The system reaches ~38% thermal efficiency at steady load.",
        0.90,
    ),
    ("doc:design", "Heat recovery is coupled to the exhaust loop by design.", 0.80),
    (
        "doc:field-trial",
        "Field trials saw a 12% efficiency drop in cold climates.",
        0.70,
    ),
    ("doc:spec", "The unit is rated 3.2 kW with a 5-minute ramp to full power.", 0.60),
]


def _strategy_of(context: Context) -> Artifact[Strategy] | None:
    strategies = context.list_artifacts(Strategy)
    return strategies[0] if strategies else None


class DepthInvestigate(Produce[Evidence]):
    """One strong finding from the top-ranked document."""

    artifact_type = Evidence

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        strategy = _strategy_of(context)
        if strategy is None or strategy.data.kind != "depth":
            return None
        context.announce("depth: wording 1 finding…", kind="status")
        doc_id, snippet, quality = _CATALOG[0]
        self.effects.create(
            Evidence(
                branch=strategy.data.branch,
                source=doc_id,
                text=await _wording(context, snippet),
                score=quality,
            ),
            id=f"evidence:{strategy.data.branch}:{doc_id}",
        )
        return None


class BreadthInvestigate(Produce[Evidence]):
    """Several weaker findings across more documents."""

    artifact_type = Evidence

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        strategy = _strategy_of(context)
        if strategy is None or strategy.data.kind != "breadth":
            return None
        for offset in range(1, min(4, len(_CATALOG))):
            context.announce(
                f"breadth: wording finding {offset}/{min(3, len(_CATALOG) - 1)}…",
                kind="status",
            )
            doc_id, snippet, quality = _CATALOG[offset]
            self.effects.create(
                Evidence(
                    branch=strategy.data.branch,
                    source=doc_id,
                    text=await _wording(context, snippet),
                    score=quality,
                ),
                id=f"evidence:{strategy.data.branch}:{doc_id}",
            )
        return None


__all__ = ["BreadthInvestigate", "DepthInvestigate"]
