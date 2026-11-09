"""knowledge search — fan-out, text and spreadsheet materialization."""

from __future__ import annotations

from typing import Annotated

from reactifact import Artifact, Consume, Context, Effects, Step, When
from reactifact.recipes import fan_out_sources, materialize_doc
from reactifact.sources import SourceRef

from ..models import ResearchTurn, SearchDone, Spreadsheet, TypedDoc
from .common import SCOUT_LIMIT


class ScoutSources(Step):
    """Fan-out over all configured sources, with idempotency (§24, §42).

    Effects-based: `fan_out_sources` writes the refs into `effects`, this
    produce adds the idempotency marker, and the runtime commits the slot once.
    """

    consumes = (ResearchTurn, Consume(SearchDone, wakes=False))
    produces = (SourceRef, SearchDone)
    scope = "query_id"

    async def run(
        self,
        context: Context,
        turn: ResearchTurn,
        searches: list[Artifact[SearchDone]],
        effects: Effects,
    ) -> None:
        # Idempotency (§42): once searched, don't restart the cascade.
        if searches:
            return None
        await fan_out_sources(
            context,
            turn.text,
            owner_id=turn.query_id,
            limit=SCOUT_LIMIT,
            on_start=lambda sid: context.announce(
                f"Searching for information in source «{sid}»...",
                kind="status",
                source=sid,
            ),
            on_count=lambda sid, n: context.announce(
                f"Found {n} matches in «{sid}»",
                kind="status",
                count=n,
                source=sid,
            ),
        )
        effects.create(
            SearchDone(query_id=turn.query_id), id=f"scouted:{turn.query_id}"
        )
        return None


class ResolveRef(Step):
    """Lazy materialization of a text ref (§6); text documents only."""

    consumes = (
        Annotated[SourceRef, When(lambda ref: not ref.metadata.get("structured"))],
    )
    produces = (TypedDoc,)

    async def run(self, context: Context, ref_artifact: Artifact[SourceRef]) -> None:
        ref = ref_artifact.data
        context.announce(
            f"Reading document «{ref.locator}» from {ref.source_id}...",
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

        # provenance (§34): TypedDoc —resolved_from→ SourceRef
        await materialize_doc(
            context, ref_artifact, doc_factory, relation="resolved_from"
        )
        return None


class ResolveTable(Step):
    """Spreadsheet: structured data stays structured, not text (§29)."""

    consumes = (
        Annotated[SourceRef, When(lambda ref: bool(ref.metadata.get("structured")))],
    )
    produces = (Spreadsheet,)

    async def run(
        self,
        context: Context,
        ref_artifact: Artifact[SourceRef],
        effects: Effects,
    ) -> None:
        ref = ref_artifact.data
        source = context.resources.get_source(ref.source_id)
        if source is None:
            return None
        context.announce(
            f"Reading spreadsheet «{ref.locator}» from {ref.source_id}...",
            kind="status",
            source=ref.source_id,
            path=ref.locator,
        )
        try:
            payload = await source.resolve(ref)
        except Exception:
            return None
        columns = list(payload.get("columns", []))
        rows = [list(r) for r in payload.get("rows", [])]
        sheet_id = f"sheet:{ref.stable_id()}"
        sheet = effects.create(
            Spreadsheet(
                source_id=ref.source_id,
                path=ref.locator,
                columns=columns,
                rows=rows,
                query_id=ref.query_id,
            ),
            id=sheet_id,
        )
        sheet.link("materialized_from", ref_artifact)
        return None
