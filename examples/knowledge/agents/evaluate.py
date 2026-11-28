"""Turn status machine and the final answer projection."""

from __future__ import annotations

import logging

from reactifact import (
    Agent,
    Artifact,
    Consume,
    Context,
    Emit,
)
from reactifact.agent import agent
from reactifact.recipes import StatusMachine, StatusState, match_skills
from reactifact.sources import SourceRef
from reactifact.structured import structured_llm

from ..models import (
    Answer,
    AnswerBody,
    Calculation,
    Claim,
    Evidence,
    ResearchTurn,
    SearchDone,
    Spreadsheet,
    TypedDoc,
)
from .common import FALLBACK_TOPN, conversation_text

logger = logging.getLogger(__name__)


def _log_llm_outage(reason: str, exc: BaseException | None) -> None:
    """Only a provider failure is worth an alert; offline/parse errors are not."""
    if reason == "provider_error":
        logger.warning("BuildAnswer: LLM provider call failed: %r", exc)


class EvaluateTurn(StatusMachine[ResearchTurn]):
    """Moves the turn's status when state changes (deterministic, §24, §69)."""

    reads = (
        ResearchTurn,
        SourceRef,
        TypedDoc,
        Evidence,
        Claim,
        Spreadsheet,
        Calculation,
        SearchDone,
        Answer,
    )
    produces = (ResearchTurn,)
    terminal = frozenset({"answered", "insufficient"})

    def next_status(self, state: StatusState[ResearchTurn]) -> str | None:
        if state.any(Answer):
            return "answered"
        if state.any(SourceRef) and (state.any(Evidence) or state.any(Calculation)):
            return "answerable"
        if state.any(SearchDone) and not state.any(SourceRef):
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


#: Deterministic turn overseer: one agent around the status-machine step.
ProgressEvaluator = Agent(
    "progress_evaluator",
    consumes=[
        Consume(ResearchTurn),
        Consume(SourceRef),
        Consume(TypedDoc),
        Consume(Evidence),
        Consume(Claim),
        Consume(Spreadsheet),
        Consume(Calculation),
        Consume(SearchDone),
        Consume(Answer),
    ],
    steps=[EvaluateTurn()],
    priority=100,
)


@agent(
    name="answer_builder",
    consumes=[Consume.by_field(ResearchTurn, "status", "answerable")],
    produces=Answer,
    scope="query_id",
)
async def build_answer(
    context: Context,
    turn: ResearchTurn,
    evidences: list[Artifact[Evidence]],
    claims: list[Artifact[Claim]],
    calculations: list[Artifact[Calculation]],
    spreadsheets: list[Artifact[Spreadsheet]],
) -> Emit | None:
    """Projection of all the query's evidence into an answer (§17, §34)."""
    query_id = turn.query_id
    if not evidences and not calculations:
        return None

    context.announce(
        f"Assembling the answer from {len(evidences)} facts and "
        f"{len(calculations)} calculation(s)...",
        kind="status",
        count=len(evidences) + len(calculations),
    )
    source_by_evidence = {evidence.id: evidence.data.source for evidence in evidences}
    materials: list[tuple[str, str]] = []
    if claims:
        claims.sort(key=lambda claim: claim.data.confidence, reverse=True)
        for claim in claims:
            linked = context.related(claim.id, relation="derived_from")
            source = source_by_evidence.get(linked[0].id) if linked else "?"
            status = "conflict" if claim.data.conflict else claim.data.status
            meta = f"{source} [conf {claim.data.confidence:g}, {status}]"
            materials.append((meta, claim.data.text))
    else:
        evidences.sort(key=lambda evidence: evidence.data.score, reverse=True)
        materials.extend(
            (
                f"{evidence.data.source} [score {evidence.data.score:g}]",
                evidence.data.text,
            )
            for evidence in evidences
        )
    for calc in calculations:
        materials.append(("calc", f"{calc.data.description} = {calc.data.value}"))

    conflicts = [claim for claim in claims if claim.data.conflict]
    material_lines = "\n".join(f"- {meta}: {text}" for meta, text in materials)
    if conflicts and claims:
        material_lines += (
            f"\n\n⚠ Contradictions found between claims (n={len(conflicts)})."
        )
    question = turn.text
    conversation = conversation_text(context, query_id)
    prompt = "Assemble a coherent answer to the question based on the facts."
    if calculations:
        skills = context.resources.get("skills") or []
        situation = (
            "reporting an answer backed by a number computed from structured "
            "storage (a spreadsheet/CSV table)"
        )
        for skill in match_skills(skills, situation):
            prompt += f"\n\nInstruction ({skill.name}): {skill.body}"
    if conversation:
        prompt += f"\n\n{conversation}"
    prompt += f"\nQuestion: {question}\nFacts:\n{material_lines}"
    body = await structured_llm(
        context.resources.llm,
        schema=AnswerBody,
        user=prompt,
        on_error=_log_llm_outage,
    )
    sources = [evidence.data.source for evidence in evidences] + [
        f"{calc.data.source_id}:{calc.data.path}" for calc in spreadsheets
    ]
    if body is not None:
        text = body.text.strip()
    else:
        parts: list[str] = []
        top = materials[:FALLBACK_TOPN]
        for meta, material in top:
            if meta == "calc" or meta.startswith("calc"):
                parts.append(f"• Calc: {material}")
            else:
                parts.append(f"• {meta}: {material}")
        calc_note = (
            (
                "\n\nAlso: "
                + "; ".join(
                    f"{calc.data.description} = {calc.data.value}"
                    for calc in calculations
                )
            )
            if calculations
            else ""
        )
        text = (
            "Failed to assemble a coherent answer. "
            "Here are the most relevant documentation fragments and calculations:"
            "\n\n" + "\n\n".join(parts) + calc_note
        )
    context.announce(
        f"Done: {len(sources)} sources",
        kind="status",
        done=True,
        sources=sources,
    )
    answer_id = f"answer:{query_id}"
    links = [("supported_by", artifact) for artifact in (*evidences, *calculations)]
    return Emit(
        Answer(query_id=query_id, text=text, sources=sources),
        id=answer_id,
        links=tuple(links),
    )


__all__ = ["EvaluateTurn", "ProgressEvaluator", "build_answer"]
