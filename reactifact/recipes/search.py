"""recipes — ready-made search fan-out (§8, §24, §42).

Effects-based: fans out over every configured source and *writes* the ranked,
idempotent `SourceRef`s into the current produce's `effects` slot instead of
returning a patch to merge by hand. Returns the scoped refs so the caller can
inspect/rank them and add its own marker effect (e.g. `SearchDone`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..context import Context
from ..sources import SourceRef

if TYPE_CHECKING:
    from ..effects import Effects


def _active_effects(context: Context) -> Effects:
    from ..effects import current_effects

    effects = current_effects()
    if effects is None:
        raise RuntimeError(
            "fan_out_sources/materialize_doc must run inside a produce "
            "(the runtime provides `effects` for the turn)"
        )
    return effects


async def fan_out_sources(
    context: Context,
    query: str,
    owner_id: str,
    *,
    limit: int = 5,
    query_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
    on_start: Callable[[str], None] | None = None,
    on_count: Callable[[str, int], None] | None = None,
) -> list[SourceRef]:
    """Fans out over every configured source and builds idempotent refs in `effects`.

    Each `SourceRef` becomes a `create(ref, id=f"ref:{ref.stable_id()}:{owner_id}")`
    in the current produce's effect slot (§42), so a repeated fan-out for the same
    owner re-creates the same ids (create-or-refresh). `on_start` / `on_count`
    receive each source's id and hit count for progress announces. The returned
    list is the scoped, ranked refs — the caller may inspect them and add its
    own "search done" marker effect.
    """
    effects = _active_effects(context)
    refs: list[SourceRef] = []
    for source in context.resources.sources.values():
        if on_start is not None:
            on_start(source.source_id)
        found = await source.asearch(query, limit=limit)
        if on_count is not None:
            on_count(source.source_id, len(found))
        refs.extend(found)
    refs.sort(key=lambda r: r.score or 0.0, reverse=True)

    scoped_refs: list[SourceRef] = []
    for ref in refs[:limit]:
        scoped = ref.model_copy(
            update={
                "metadata": {
                    **ref.metadata,
                    "owner_id": owner_id,
                    **(extra_metadata or {}),
                },
                "query_id": query_id if query_id is not None else owner_id,
            }
        )
        scoped_refs.append(scoped)
        effects.create(scoped, id=f"ref:{ref.stable_id()}:{owner_id}")
    return scoped_refs


__all__ = ["fan_out_sources"]
