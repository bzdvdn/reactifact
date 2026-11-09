"""knowledge lifecycle — deterministic turn overseer and the answer builder."""

from __future__ import annotations

import logging
from typing import Annotated

from reactifact import Artifact, Consume, Context, Effects, Step, When
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
    """structured_llm's on_error: only "provider_error" is worth an alert —
    "no_provider" is just offline demo mode, "parse_error" is the model
    replying with something unparseable, not an outage."""
    if reason == "provider_error":
        logger.warning("BuildAnswer: LLM provider call failed: %r", exc)


class EvaluateTurn(StatusMachine[ResearchTurn]):
    """Moves the turn's status when state changes (deterministic, §24, §69)."""

    consumes = (
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

    def next_status(self, state: StatusState) -> str | None:
        """Pure function: which status the turn deserves in the current state."""
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


class BuildAnswer(Step):
    """Projection of all the query's evidence into an answer (§17, §34)."""

    consumes = (
        Annotated[ResearchTurn, When(lambda turn: turn.status == "answerable")],
        Consume(Evidence, wakes=False),
        Consume(Claim, wakes=False),
        Consume(Calculation, wakes=False),
        Consume(Spreadsheet, wakes=False),
    )
    produces = (Answer,)
    scope = "query_id"

    async def run(
        self,
        context: Context,
        turn: ResearchTurn,
        evidences: list[Artifact[Evidence]],
        claims: list[Artifact[Claim]],
        calculations: list[Artifact[Calculation]],
        spreadsheets: list[Artifact[Spreadsheet]],
        effects: Effects,
    ) -> None:
        query_id = turn.query_id
        if not evidences and not calculations:
            return None

        context.announce(
            f"Assembling the answer from {len(evidences)} facts and "
            f"{len(calculations)} calculation(s)...",
            kind="status",
            count=len(evidences) + len(calculations),
        )
        source_by_evidence = {e.id: e.data.source for e in evidences}
        materials: list[tuple[str, str]] = []
        if claims:
            claims.sort(key=lambda c: c.data.confidence, reverse=True)
            for claim in claims:
                linked = context.related(claim.id, relation="derived_from")
                source = source_by_evidence.get(linked[0].id) if linked else "?"
                status = "conflict" if claim.data.conflict else claim.data.status
                meta = f"{source} [conf {claim.data.confidence:g}, {status}]"
                materials.append((meta, claim.data.text))
        else:
            evidences.sort(key=lambda e: e.data.score, reverse=True)
            materials.extend(
                (f"{e.data.source} [score {e.data.score:g}]", e.data.text)
                for e in evidences
            )
        for calc in calculations:
            materials.append(("calc", f"{calc.data.description} = {calc.data.value}"))

        conflicts = [c for c in claims if c.data.conflict]
        material_lines = "\n".join(f"- {meta}: {text}" for meta, text in materials)
        if conflicts and claims:
            material_lines += (
                f"\n\n⚠ Contradictions found between claims (n={len(conflicts)})."
            )
        question = turn.text
        conversation = conversation_text(context, query_id)
        prompt = "Assemble a coherent answer to the question based on the facts."
        if calculations:
            # Skills (§67): a matched skill's body is a rule to follow for
            # *this* turn, chosen by what's happening (a computed total is
            # in play), not by parsing the user's raw wording.
            skills = context.resources.get("skills") or []
            situation = "reporting an answer backed by a number computed from structured storage (a spreadsheet/CSV table)"
            for skill in match_skills(skills, situation):
                prompt += f"\n\nInstruction ({skill.name}): {skill.body}"
        if conversation:
            prompt += f"\n\n{conversation}"
        prompt += f"\nQuestion: {question}\nFacts:\n{material_lines}"
        body = await structured_llm(
            context, schema=AnswerBody, user=prompt, on_error=_log_llm_outage
        )
        sources = [e.data.source for e in evidences] + [
            f"{c.data.source_id}:{c.data.path}" for c in spreadsheets
        ]
        if body is not None:
            text = body.text.strip()
        else:
            # Without an LLM (or the model gave no valid answer) — an honest
            # fallback (§68): the most confident facts, then calculations.
            parts: list[str] = []
            top = materials[:FALLBACK_TOPN]
            for meta, mtext in top:
                if meta == "calc" or meta.startswith("calc"):
                    parts.append(f"• Calc: {mtext}")
                else:
                    parts.append(f"• {meta}: {mtext}")
            calc_note = (
                (
                    "\n\nAlso: "
                    + "; ".join(
                        f"{c.data.description} = {c.data.value}" for c in calculations
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
        answer = effects.create(
            Answer(query_id=query_id, text=text, sources=sources),
            id=answer_id,
        )
        for evidence_art in evidences:
            answer.link("supported_by", evidence_art)
        for calc in calculations:
            answer.link("supported_by", calc)
        return None
