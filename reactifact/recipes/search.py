"""recipes — ready-made search fan-out (§8, §24, §42).

`search_sources` is the pure part: it queries every configured source and
returns the ranked, scoped `SourceRef`s. `fan_out_sources` is the effect-slot
wrapper: it also writes each ref as a `create-or-refresh` artifact under a
stable id, so a repeated fan-out for the same owner stays idempotent. A step
that wants the return-style form can call `search_sources` and return
`Emit(ref, id=source_ref_id(ref, owner_id))` instructions itself.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, overload

from ..context import Context
from ..sources import SourceRef
from ._effects import require_effects

if TYPE_CHECKING:
    from ..effects import Effects, Handle


#: The stable id `fan_out_sources` gives a ref for `owner_id`.
def source_ref_id(ref: SourceRef, owner_id: str) -> str:
    return f"ref:{ref.stable_id()}:{owner_id}"


async def search_sources(
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
    """Fan out over every configured source and return the scoped refs.

    Pure: this function does not touch the effect slot. `on_start` / `on_count`
    receive each source's id and hit count for progress announces.
    """
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
        scoped_refs.append(
            ref.model_copy(
                update={
                    "metadata": {
                        **ref.metadata,
                        "owner_id": owner_id,
                        **(extra_metadata or {}),
                    },
                    "query_id": query_id if query_id is not None else owner_id,
                }
            )
        )
    return scoped_refs


@overload
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
    as_artifacts: Literal[False] = False,
) -> list[SourceRef]: ...


@overload
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
    as_artifacts: Literal[True],
) -> list[Handle]: ...


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
    as_artifacts: bool = False,
    effects: Effects | None = None,
) -> list[SourceRef] | list[Handle]:
    """`search_sources` plus create-or-refresh refs in the current effect slot.

    Each ref becomes `create(ref, id=source_ref_id(ref, owner_id))` (§42), so a
    repeated fan-out for the same owner re-creates the same ids. By default the
    scoped, ranked refs are returned as data. Pass `as_artifacts=True` to get
    planned `Handle`s instead — the shape `materialize_doc` accepts in the same
    produce, without a lookup after commit. Pass `effects=` to use an explicit
    slot instead of the current produce's.
    """
    refs = await search_sources(
        context,
        query,
        owner_id,
        limit=limit,
        query_id=query_id,
        extra_metadata=extra_metadata,
        on_start=on_start,
        on_count=on_count,
    )
    slot = require_effects(context, effects)
    handles = [slot.create(ref, id=source_ref_id(ref, owner_id)) for ref in refs]
    return handles if as_artifacts else refs


__all__ = ["fan_out_sources", "search_sources", "source_ref_id"]
