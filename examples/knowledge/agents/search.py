"""Fan-out, text and spreadsheet materialization."""

from __future__ import annotations

from reactifact import Artifact, Consume, Context, Emit
from reactifact.agent import agent
from reactifact.recipes import fan_out_sources, materialize_doc
from reactifact.sources import SourceRef

from ..models import ResearchTurn, SearchDone, Spreadsheet, TypedDoc
from .common import SCOUT_LIMIT


@agent(
    name="search_scout",
    consumes=[Consume(ResearchTurn)],
    produces=(SourceRef, SearchDone),
    scope="query_id",
)
async def search_sources(
    context: Context,
    turn: ResearchTurn,
    searches: list[Artifact[SearchDone]],
) -> Emit | None:
    """Fan-out over all configured sources, with idempotency (§24, §42)."""
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
    return Emit(SearchDone(query_id=turn.query_id), id=f"scouted:{turn.query_id}")


@agent(
    name="resolver",
    consumes=[
        Consume(
            SourceRef,
            condition=lambda artifact: not artifact.data.metadata.get("structured"),
        )
    ],
    produces=TypedDoc,
)
async def resolve_text(context: Context, ref_artifact: Artifact[SourceRef]) -> None:
    """Lazy materialization of a text ref (§6); text documents only."""
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

    await materialize_doc(context, ref_artifact, doc_factory, relation="resolved_from")
    return None


@agent(
    name="table_resolver",
    consumes=[
        Consume(
            SourceRef,
            condition=lambda artifact: bool(artifact.data.metadata.get("structured")),
        )
    ],
    produces=Spreadsheet,
)
async def resolve_table(
    context: Context,
    ref_artifact: Artifact[SourceRef],
) -> Emit | None:
    """Spreadsheet: structured data stays structured, not text (§29)."""
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
    rows = [list(row) for row in payload.get("rows", [])]
    sheet_id = f"sheet:{ref.stable_id()}"
    return Emit(
        Spreadsheet(
            source_id=ref.source_id,
            path=ref.locator,
            columns=columns,
            rows=rows,
            query_id=ref.query_id,
        ),
        id=sheet_id,
        links=(("materialized_from", ref_artifact),),
    )


__all__ = ["resolve_table", "resolve_text", "search_sources"]
