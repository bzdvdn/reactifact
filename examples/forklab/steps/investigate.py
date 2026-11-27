"""forklab — deterministic investigation strategies with LLM wording (§24, §67).

Each strategy picks its documents and scores them *deterministically* (no LLM —
the branching, scoring and provenance are the demonstration). The model is used
only where generation is genuinely needed: wording each finding in natural
language. If no model is configured, the snippet text is used as-is (honest
fallback, §59) — the pipeline still runs fully offline.
"""

from __future__ import annotations

from reactifact import Artifact, Context, Emit, Step
from reactifact.structured import structured_llm

from ..models import Evidence, EvidenceBody, Question, Strategy
from ..prompts import wording_prompt


async def _wording(context: Context, snippet: str) -> str:
    """LLM phrasing of a finding; the deterministic snippet on no-model/parse-fail.

    The model always knows the *topic* and the *question* it is wording for
    (from the live `Question` artifact) — it never works without domain context (§68).
    """
    if context.resources.llm is None:
        return snippet
    questions = context.list_artifacts(Question)
    question = questions[0].data if questions else None
    rendered = wording_prompt(
        topic=question.topic if question else "unknown",
        question=question.text if question else "unknown",
        snippet=snippet,
    )
    body = await structured_llm(
        context.resources.llm,
        schema=EvidenceBody,
        **rendered.as_kwargs(),
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


class DepthInvestigate(Step):
    """One strong finding from the top-ranked document."""

    reads = (Strategy,)
    produces = (Evidence,)

    async def run(
        self,
        strategy: Artifact[Strategy],
        context: Context,
    ) -> Emit | None:
        if strategy.data.kind != "depth":
            return None
        self.progress("depth: wording 1 finding…")
        doc_id, snippet, quality = _CATALOG[0]
        return Emit(
            Evidence(
                branch=strategy.data.branch,
                source=doc_id,
                text=await _wording(context, snippet),
                score=quality,
            ),
            id=f"evidence:{strategy.data.branch}:{doc_id}",
        )


class BreadthInvestigate(Step):
    """Several weaker findings across more documents."""

    reads = (Strategy,)
    produces = (Evidence,)

    async def run(
        self,
        strategy: Artifact[Strategy],
        context: Context,
    ) -> list[Emit] | None:
        if strategy.data.kind != "breadth":
            return None
        findings: list[Emit] = []
        for offset in range(1, min(4, len(_CATALOG))):
            self.progress(
                f"breadth: wording finding {offset}/{min(3, len(_CATALOG) - 1)}…"
            )
            doc_id, snippet, quality = _CATALOG[offset]
            findings.append(
                Emit(
                    Evidence(
                        branch=strategy.data.branch,
                        source=doc_id,
                        text=await _wording(context, snippet),
                        score=quality,
                    ),
                    id=f"evidence:{strategy.data.branch}:{doc_id}",
                )
            )
        return findings


__all__ = ["BreadthInvestigate", "DepthInvestigate"]
