"""medic-lab steering: HITL ask → deepen or report (§60)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel
from reactifact import Context, Produce, ProduceCall
from reactifact.interrupt import PendingQuestion
from reactifact.structured import StructuredLLM

from ..models import (
    Hypothesis,
    HypothesisRank,
    Question,
    ResearchReport,
)
from ..prompts import SYSTEM_DEEPEN, SYSTEM_REPORTER
from .common import (
    MAX_DEPTH,
    all_terminal,
    hypotheses_of,
    question_id_of,
)


class _DeepenQuery(BaseModel):
    """Structured-LLM schema: clarifying questions for a deepen round."""

    questions: list[str] = []


class _ReportBody(BaseModel):
    """Structured-LLM schema: the synthesized lab answer."""

    text: str = ""


#: Reusable roles (schema + system): only `user` varies per call (§66).
_deepen_prompt = StructuredLLM(_DeepenQuery, system=SYSTEM_DEEPEN)
_report_prompt = StructuredLLM(_ReportBody, system=SYSTEM_REPORTER)


def _vertex_label(status: str) -> str:
    return {
        "supported": "supported",
        "refuted": "refuted",
        "inconclusive": "unresolved",
    }.get(status, status)


def _ranking_block(context: Context, question_id: str) -> str:
    lines = []
    for i, hyp_art in enumerate(hypotheses_of(context, question_id)):
        d = hyp_art.data
        lines.append(
            f"  [{i}] {_vertex_label(d.status)}, score {d.score:.2f} "
            f"({d.supports} for / {d.contradicts} against)\n"
            f"       «{d.statement}»"
        )
    return "\n".join(lines)


class Steer(Produce[PendingQuestion]):
    """HITL (§60): when every hypothesis is terminal, ask the human.

    Uses a stable artifact id, so concurrent invocations in one generation
    collapse into a single question (§42): the intentional no-op updates make
    no new events.
    """

    artifact_type = PendingQuestion

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        question_id = question_id_of(context, call.event)
        if question_id is None:
            return None
        question = context.get(question_id)
        if question is None or not isinstance(question.data, Question):
            return None
        if context.list_artifacts(ResearchReport):
            return None
        if question.data.depth >= MAX_DEPTH:
            return None  # auto-report handles it
        if not all_terminal(context, question_id):
            return None
        # do not re-ask while an unanswered steer question exists
        steer_id = f"steer:{question_id}:{question.data.depth}"
        if context.get(steer_id) is not None:
            return None  # same round already steering (idempotent, §42)
        self.effects.ask(
            "All hypotheses have been evaluated. Deepen one, "
            "or stop for the final report?\n\n"
            f"{_ranking_block(context, question_id)}\n\n"
            "Reply with a number (0-3) to deepen that hypothesis, "
            "or «stop» for the report.",
            kind="steer",
            notes={"question_id": question_id},
            id=steer_id,
        )
        return None


class Deepen(Produce[PendingQuestion]):
    """Human answer: deepen hypothesis N (reopens its channel) or stop."""

    artifact_type = PendingQuestion

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        q_art = call.trigger
        if q_art is None or not isinstance(q_art.data, PendingQuestion):
            return None
        q = q_art.data
        if not q.answered or q.kind != "steer":
            return None
        question_id = q.notes.get("question_id")
        if not question_id:
            return None
        answer = (q.resolution or "").strip().lower()
        if answer.startswith("stop") or answer in {"no", "done", "хватит", "стоп"}:
            return None  # Reporter builds the report
        m = re.fullmatch(r"[h]?(\d)", answer)
        if m is not None:
            index = int(m.group(1))
            hypotheses = hypotheses_of(context, question_id)
            if 0 <= index < len(hypotheses):
                hyp_art = hypotheses[index]
                if hyp_art.data.status != "open":
                    context.announce(
                        f"Deepening hypothesis {index}: «{hyp_art.data.statement[:40]}…»",
                        kind="status",
                        hypothesis=hyp_art.id,
                    )
                    self.effects.update(hyp_art, status="open")
                    question = context.get(question_id)
                    if question is not None and isinstance(question.data, Question):
                        if context.resources.llm is not None:
                            context.announce(
                                "Deriving clarifying questions from the model…",
                                kind="status",
                            )
                        queries = await self._deepen_queries(
                            context, question.data, hyp_art.data
                        )
                        updates: dict[str, Any] = {"depth": question.data.depth + 1}
                        if queries:
                            existing = dict(question.data.deepen_queries or {})
                            existing[hyp_art.id] = queries
                            updates["deepen_queries"] = existing
                        self.effects.update(question, **updates)
        return None

    async def _deepen_queries(
        self, context: Context, question: Question, hyp: Hypothesis
    ) -> list[str]:
        """Model-proposed clarifying questions for the deepen round (§66).

        Without an LLM no extra questions are produced — the round simply
        re-searches the hypothesis (§67).
        """
        if context.resources.llm is None:
            return []
        body = await _deepen_prompt.call(
            context,
            user=(
                f"Hypothesis to deepen: «{hyp.statement}»\n"
                f"Original question: {question.text}"
            ),
        )
        return [q.strip() for q in body.questions if q.strip()] if body else []


class Reporter(Produce[ResearchReport]):
    """Builds the report when the human stops or the depth budget is exhausted."""

    artifact_type = ResearchReport

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        question_id = question_id_of(context, call.event)
        if question_id is None:
            return None
        if context.list_artifacts(ResearchReport):
            return None
        remain_steer = [
            q
            for q in context.pending_questions()
            if q.data.notes.get("question_id") == question_id
        ]
        question = context.get(question_id)
        if question is None or not isinstance(question.data, Question):
            return None
        depth = question.data.depth
        # build only when: a steer was answered with «stop», or depth budget ran out
        answered_stop = any(
            q.data.answered
            and (q.data.resolution or "").strip().lower().startswith("stop")
            for q in context.list_artifacts(PendingQuestion)
            if q.data.kind == "steer" and q.data.notes.get("question_id") == question_id
        )
        if not answered_stop and depth < MAX_DEPTH:
            return None
        if not all_terminal(context, question_id):
            return None
        if remain_steer:
            return None  # human still deciding

        context.announce("Compiling the research report...", kind="status")
        ranking: list[HypothesisRank] = []
        for hyp_art in hypotheses_of(context, question_id):
            d = hyp_art.data
            ranking.append(
                HypothesisRank(
                    hypothesis_id=hyp_art.id,
                    statement=d.statement,
                    score=d.score,
                    supports=d.supports,
                    contradicts=d.contradicts,
                    coverage=d.coverage,
                    confidence=d.confidence,
                    verdict=d.status,
                )
            )
        ranking.sort(key=lambda r: r.score, reverse=True)
        question_text = question.data.text
        if context.resources.llm is not None and ranking:
            context.announce(
                "Synthesizing the final report with the model…", kind="status"
            )
        synthesized = await self._synthesize(context, question_text, ranking)
        if synthesized is None:
            if ranking:
                top = ranking[0]
                synthesized = f"Most supported: «{top.statement}» (score {top.score})."
                parts = ["not all alternatives are closed"]
                inconclusive = [r for r in ranking if r.verdict == "inconclusive"]
                refuted = [r for r in ranking if r.verdict == "refuted"]
                if inconclusive:
                    parts.append(
                        f"{len(inconclusive)} hypothesis(es) remain inconclusive"
                    )
                if refuted:
                    parts.append(f"{len(refuted)} are contradicted by the sources")
                uncertainty_ = "; ".join(parts) + "."
            else:
                synthesized = "No hypothesis could be evaluated."
                uncertainty_ = "insufficient evidence."
        else:
            uncertainty_ = "see the ranked hypotheses below."
        self.effects.create(
            ResearchReport(
                question_id=question_id,
                answer=synthesized,
                uncertainty=uncertainty_,
                ranking=ranking,
            ),
            id=f"report:{question_id}",
        )
        return None

    async def _synthesize(
        self,
        context: Context,
        question_text: str,
        ranking: list[HypothesisRank],
    ) -> str | None:
        """Asks the model to write the final lab answer from the ranking (§66).

        Deterministic fallback in the caller when the model is absent (§67).
        """
        if context.resources.llm is None or not ranking:
            return None
        lines = "\n".join(
            f"- {r.statement} [score {r.score}, {r.verdict}, "
            f"{r.supports} for / {r.contradicts} against]"
            for r in ranking
        )
        body = await _report_prompt.call(
            context,
            user=(
                f"Laboratory question: {question_text}\n"
                f"Hypothesis ranking:\n{lines}\n\n"
                "Write 2-4 sentences: which hypothesis is best supported, how "
                "strongly, and what remains uncertain."
            ),
        )
        text = body.text.strip() if body else ""
        return text or None
