"""research stages — routing, web scout, materialization, extraction."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Artifact, Context, Produce, ProduceCall
from reactifact.recipes import fan_out_sources, materialize_doc
from reactifact.sources import SourceRef
from reactifact.structured import StructuredLLM

from ..models import (
    Evidence,
    ResearchTurn,
    SearchDone,
    TypedDoc,
    UserQuery,
)
from .common import SCOUT_LIMIT, turn_of, user_query


class Router(Produce[ResearchTurn]):
    """Every non-empty question becomes a research turn."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        event = call.event
        user = user_query(context, event)
        if user is None or event is None:
            return None
        text = user.text.strip()
        if not text:
            return None
        context.announce("Question requires web research", kind="status")
        self.effects.create(
            ResearchTurn(query_id=event.artifact_id, text=text, status="researching")
        )
        return None


class WebScout(Produce[SourceRef]):
    """Fans out to `WebSource.asearch`; stable refs + SearchDone (idempotent)."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        turn = turn_of(context, call.event)
        if turn is None:
            return None
        # Idempotency (§42): a repeated run must not restart the cascade.
        if any(
            s.data.query_id == turn.query_id for s in context.list_artifacts(SearchDone)
        ):
            return None

        await fan_out_sources(
            context,
            turn.text,
            owner_id=turn.query_id,
            limit=SCOUT_LIMIT,
            on_start=lambda sid: context.announce(
                f"Searching the web in «{sid}»...",
                kind="status",
                source=sid,
            ),
            on_count=lambda sid, n: context.announce(
                f"Found {n} pages in «{sid}»",
                kind="status",
                count=n,
                source=sid,
            ),
        )
        self.effects.create(
            SearchDone(query_id=turn.query_id), id=f"scouted:{turn.query_id}"
        )
        return None


class ResolveRef(Produce[TypedDoc]):
    """Lazy materialization: a URL → page text (Reference → Artifact, §6)."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        ref_artifact = call.trigger
        if ref_artifact is None or not isinstance(ref_artifact.data, SourceRef):
            return None
        ref = ref_artifact.data
        context.announce(
            f"Fetching page «{ref.locator}»...",
            kind="status",
            source=ref.source_id,
            path=ref.locator,
        )

        def doc_factory(
            _context: Context, _ref_art: Artifact[SourceRef], content: str
        ) -> TypedDoc:
            data = _ref_art.data
            return TypedDoc(
                source_id=data.source_id,
                path=data.locator,
                content=content,
                query_id=data.query_id,
                score=data.score or 0.0,
            )

        await materialize_doc(
            context, ref_artifact, doc_factory, relation="resolved_from"
        )
        return None


class _Digest(BaseModel):
    """Structured-LLM schema: a neutral page digest."""

    text: str = ""


_extract_prompt = StructuredLLM(_Digest)


class ExtractEvidence(Produce[Evidence]):
    """Key facts from a page; provenance: Evidence —extracted_from→ Doc."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        doc_artifact = call.trigger
        if doc_artifact is None or not isinstance(doc_artifact.data, TypedDoc):
            return None
        doc = doc_artifact.data
        context.announce(
            f"Extracting key facts from «{doc.path}»...",
            kind="status",
            path=doc.path,
        )
        body = await _extract_prompt.call(
            context,
            user=f"Extract a concise digest of the facts from this page:\n{doc.content}",
        )
        text = body.text.strip() if body else " ".join(doc.content.split())[:200]
        evidence_id = f"evidence:{doc.query_id}:{doc.path}"
        evidence = self.effects.create(
            Evidence(
                query_id=doc.query_id,
                text=text,
                source=doc.path,
                score=doc.score,
            ),
            id=evidence_id,
        )
        evidence.link("extracted_from", doc_artifact)
        return None
