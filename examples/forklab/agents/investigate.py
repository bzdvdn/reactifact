"""forklab — one agent, two deterministic investigation strategies."""

from __future__ import annotations

from reactifact import Artifact, Consume, Context, Emit, Progress
from reactifact.agent import agent
from reactifact.structured import structured_llm

from ..models import Evidence, EvidenceBody, Question, Strategy
from ..prompts import wording_prompt

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


async def _wording(context: Context, snippet: str) -> str:
    """LLM phrasing of a finding; the deterministic snippet on no-model/parse-fail."""
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


@agent(consumes=[Consume(Strategy)], produces=Evidence)
async def investigate(
    strategy: Artifact[Strategy],
    context: Context,
    progress: Progress,
) -> list[Emit] | Emit | None:
    """Depth: one strong finding; breadth: several weaker ones."""
    data = strategy.data
    if data.kind == "depth":
        progress("depth: wording 1 finding…")
        doc_id, snippet, quality = _CATALOG[0]
        return Emit(
            Evidence(
                branch=data.branch,
                source=doc_id,
                text=await _wording(context, snippet),
                score=quality,
            ),
            id=f"evidence:{data.branch}:{doc_id}",
        )
    if data.kind == "breadth":
        findings: list[Emit] = []
        for offset in range(1, min(4, len(_CATALOG))):
            progress(f"breadth: wording finding {offset}/{min(3, len(_CATALOG) - 1)}…")
            doc_id, snippet, quality = _CATALOG[offset]
            findings.append(
                Emit(
                    Evidence(
                        branch=data.branch,
                        source=doc_id,
                        text=await _wording(context, snippet),
                        score=quality,
                    ),
                    id=f"evidence:{data.branch}:{doc_id}",
                )
            )
        return findings
    return None


__all__ = ["investigate"]
