"""medic-lab stages: hypotheses, per-hypothesis channels, facts, contradictions."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel
from reactifact import Artifact, Context, Effects, Step, StepCall, When
from reactifact.recipes import fan_out_sources, materialize_doc
from reactifact.sources import SourceRef
from reactifact.structured import StructuredLLM

from ..models import (
    Claim,
    Evidence,
    Hypothesis,
    Question,
    SearchDone,
    TypedDoc,
)
from ..prompts import SYSTEM_EXTRACTOR, SYSTEM_RESEARCHER
from .common import (
    SCOUT_LIMIT,
    hypotheses_of,
    negatory,
    question_id_of,
    split_sentences,
    token_support,
)


class HypothesesOut(BaseModel):
    """Structured-LLM schema: alternative hypotheses for the question."""

    hypotheses: list[str] = []


class Body(BaseModel):
    """Structured-LLM schema: a neutral digest of one page."""

    text: str = ""


_hypotheses_prompt = StructuredLLM(HypothesesOut, system=SYSTEM_RESEARCHER)
_extract_prompt = StructuredLLM(Body, system=SYSTEM_EXTRACTOR)


class Generator(Step):
    """Question → competing hypotheses.

    With an LLM available, four alternatives are proposed for *this* question
    (§66: agents are replaceable — the topic is not hardcoded). Without one,
    the deterministic set below stands in (§67).
    """

    consumes = (Question,)
    produces = (Hypothesis,)
    #: Deterministic fallback (the demo's own topic).
    STATEMENTS = (
        "vitamin D supplementation prevents winter colds",
        "vitamin D has no meaningful effect on colds",
        "vitamin D helps only in vitamin-D-deficient individuals",
        "the evidence on vitamin D and colds is mixed and insufficient",
    )
    MAX_HYPOTHESES = 4

    async def run(self, call: StepCall) -> None:
        context = call.context
        question_id = question_id_of(context, call.event)
        if question_id is None:
            return None
        if context.list_artifacts(Hypothesis):
            return None  # already generated
        question = context.get(question_id)
        question_text = question.data.text if question is not None else ""
        context.announce("Generating hypotheses...", kind="status")

        statements: list[str] = list(self.STATEMENTS)
        used_demo_topic = True
        if question_text:
            body = await _hypotheses_prompt.call(
                context,
                user=(
                    "Propose up to 4 concise, mutually exclusive candidate answers "
                    "(hypotheses) for this question. Each is a short statement; "
                    "some may deny the main claim.\n"
                    f"Question: {question_text}"
                ),
            )
            candidates = (
                [h.strip() for h in body.hypotheses if h.strip()] if body else []
            )
            if candidates:
                statements = candidates[: self.MAX_HYPOTHESES]
                used_demo_topic = False

        if used_demo_topic:
            # Honest disclosure (§59): without an LLM there is no way to turn
            # free text into hypotheses, so the lab falls back to its own
            # fixed demo topic instead of silently "investigating" something
            # unrelated to what was actually asked.
            context.announce(
                "No LLM configured — demoing with the lab's own fixed topic "
                "(vitamin D vs. colds) instead of analyzing your question.",
                kind="status",
            )

        for i, statement in enumerate(statements):
            hyp_id = f"hyp:{question_id}:{i}"
            hypothesis = call.effects.create(
                Hypothesis(question_id=question_id, statement=statement, status="open"),
                id=hyp_id,
            )
            hypothesis.link("answers", question_id)
        return None


class Investigator(Step):
    """Per-hypothesis channel: search sources tagged with the hypothesis."""

    consumes = (Annotated[Hypothesis, When(lambda hyp: hyp.status == "open")],)
    produces = (SourceRef, SearchDone)

    async def run(
        self, context: Context, hyp_art: Artifact[Hypothesis], effects: Effects
    ) -> None:
        hyp = hyp_art.data
        question = context.get(hyp.question_id)
        if question is None or not isinstance(question.data, Question):
            return None
        depth = question.data.depth
        marker = f"searched:{hyp_art.id}:{depth}"
        if any(a.id == marker for a in context.list_artifacts(SearchDone)):
            return None  # this round already searched (idempotent, §42)

        context.announce(
            f"Investigating «{hyp.statement[:40]}…»...",
            kind="status",
            hypothesis=hyp_art.id,
        )
        # A deepen round searches by the model-proposed clarifying questions if
        # any (otherwise the same pages would be re-found and re-read, §66).
        deep_queries = (
            (question.data.deepen_queries or {}).get(hyp_art.id) if depth > 0 else None
        )
        query = (
            "; ".join(deep_queries) + f" — {question.data.text}"
            if deep_queries
            else f"{hyp.statement}: {question.data.text}"
        )
        await fan_out_sources(
            context,
            query,
            owner_id=hyp_art.id,
            query_id=hyp.question_id,
            limit=SCOUT_LIMIT,
            extra_metadata={"hypothesis_id": hyp_art.id, "round": depth},
            on_start=lambda sid: context.announce(
                f"  «{sid}» searching...", kind="status", source=sid
            ),
            on_count=lambda sid, n: context.announce(
                f"  «{sid}» → {n} pages", kind="status", count=n, source=sid
            ),
        )
        effects.create(
            SearchDone(
                hypothesis_id=hyp_art.id, round=depth, question_id=hyp.question_id
            ),
            id=marker,
        )
        if hyp.status == "open":
            effects.update(hyp_art, status="investigating")
        return None


class Resolver(Step):
    """Lazy materialization: a ref → page text (Reference → Artifact, §6)."""

    consumes = (SourceRef,)
    produces = (TypedDoc,)

    async def run(self, context: Context, ref_art: Artifact[SourceRef]) -> None:
        ref = ref_art.data
        context.announce(f"Reading «{ref.locator}»...", kind="status", path=ref.locator)

        def doc_factory(
            _context: Context, _ref_art: Artifact[SourceRef], content: str
        ) -> TypedDoc:
            data = _ref_art.data
            return TypedDoc(
                question_id=data.query_id,
                hypothesis_id=data.metadata.get("hypothesis_id") or "",
                round=int(data.metadata.get("round", 0)),
                source_id=data.source_id,
                path=data.locator,
                content=content,
                score=data.score or 0.0,
            )

        await materialize_doc(context, ref_art, doc_factory, relation="resolved_from")
        return None


class ExtractEvidence(Step):
    """Evidence from a page; polarity decided by content vs each hypothesis.

    Topic-agnostic (§66): a page supports the hypotheses it agrees with
    (same positive/negative stance in the text) and contradicts the rest.
    Deterministic — the model only summarises (§67), it never scores.
    """

    consumes = (TypedDoc,)
    produces = (Evidence,)

    async def run(
        self, context: Context, doc_art: Artifact[TypedDoc], effects: Effects
    ) -> None:
        doc = doc_art.data
        body = await _extract_prompt.call(
            context, user=f"Extract the key factual claims of this page:\n{doc.content}"
        )
        text = body.text.strip() if body else " ".join(doc.content.split())[:220]
        evidence_id = f"evidence:{doc_art.id}"
        evidence = Evidence(
            question_id=doc.question_id,
            hypothesis_id=doc.hypothesis_id,
            text=text,
            source=doc.path,
            score=doc.score,
        )
        evidence_handle = effects.create(evidence, id=evidence_id)
        evidence_handle.link("extracted_from", doc_art)
        # deterministic support/contradiction per hypothesis (§67, §36)
        for hyp_art in hypotheses_of(context, doc.question_id):
            if token_support(doc.content, hyp_art.data.statement) < 0.05:
                continue  # page does not address this hypothesis — coverage only
            same_stance = negatory(doc.content) == negatory(hyp_art.data.statement)
            evidence_handle.link("supports" if same_stance else "contradicts", hyp_art)
        return None


class ClaimBuilder(Step):
    """Evidence → claims with confidence against the page (§35)."""

    consumes = (Evidence,)
    produces = (Claim,)

    async def run(
        self, context: Context, ev_art: Artifact[Evidence], effects: Effects
    ) -> None:
        evidence = ev_art.data
        docs = context.related(ev_art.id, relation="extracted_from")
        doc_text = docs[0].data.content if docs else ""
        for i, sentence in enumerate(split_sentences(evidence.text)):
            support = token_support(sentence, doc_text)
            confidence = round(min(1.0, 0.3 + 0.7 * support), 2)
            status = (
                "verified"
                if support >= 0.6
                else ("weak" if support >= 0.35 else "unverified")
            )
            claim_id = f"claim:{ev_art.id}:{i}"
            claim = effects.create(
                Claim(
                    question_id=evidence.question_id,
                    hypothesis_id=evidence.hypothesis_id,
                    text=sentence,
                    confidence=confidence,
                    status=status,
                ),
                id=claim_id,
            )
            claim.link("derived_from", ev_art)
        return None


class CrossChecker(Step):
    """Cross-hypothesis contradictions (§36): similar wording, opposite polarity."""

    consumes = (Claim,)
    produces = (Claim,)

    async def run(
        self, context: Context, claim_art: Artifact[Claim], effects: Effects
    ) -> None:
        claim = claim_art.data
        for other in context.list_artifacts(Claim):
            if (
                other.id == claim_art.id
                or other.data.question_id != claim.question_id
                or other.data.hypothesis_id == claim.hypothesis_id
            ):
                continue
            if token_support(claim.text, other.data.text) >= 0.3 and negatory(
                claim.text
            ) != negatory(other.data.text):
                effects.link(claim_art.id, "contradicted_by", other.id)
        return None
