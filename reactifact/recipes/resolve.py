"""recipes — lazy reference materialization (§6, §34).

Effects-based: resolves a ref into a document and writes the create + the
provenance link into the current produce's `effects` slot. Returns the produced
document (or None on a failure), so the caller can react to the outcome.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from ..artifacts import Artifact
from ..context import Context
from ..effects import Handle
from ..provenance import MATERIALIZED_FROM
from ..sources import SourceRef
from .search import _active_effects

DocT = TypeVar("DocT", bound=BaseModel)

#: What a caller may pass as the ref. `SourceRef` is accepted for the common
#: `fan_out_sources(...)` → `materialize_doc(...)` path; `Handle` is what
#: `fan_out_sources(..., as_artifacts=True)` returns.
RefLike = Artifact[SourceRef] | Handle | SourceRef


def _report(
    on_error: Callable[[SourceRef, BaseException | None], None] | None,
    ref: SourceRef,
    error: BaseException | None,
) -> None:
    if on_error is None:
        return
    with contextlib.suppress(Exception):  # observer must not break the turn
        on_error(ref, error)


def _doc_from_type(
    doc_type: type[DocT],
    ref: SourceRef,
    content: str,
    *,
    text_field: str,
    field_map: Mapping[str, str] | None,
) -> DocT:
    fields = getattr(doc_type, "model_fields", {})
    if text_field not in fields:
        raise TypeError(
            f"{doc_type.__name__} has no {text_field!r} field for the resolved "
            "content; pass doc_factory=... or another text_field=..."
        )
    values: dict[str, Any] = {text_field: content}
    for field in fields:
        if not isinstance(field, str) or field == text_field:
            continue
        source_field = (field_map or {}).get(field, field)
        if hasattr(ref, source_field):
            values[field] = getattr(ref, source_field)
    return doc_type(**values)


async def materialize_doc(
    context: Context,
    ref: RefLike,
    doc_factory: Callable[[Context, Any, str], DocT] | None = None,
    *,
    doc_type: type[DocT] | None = None,
    text_field: str = "text",
    field_map: Mapping[str, str] | None = None,
    relation: str = MATERIALIZED_FROM,
    refresh: bool = True,
    on_error: Callable[[SourceRef, BaseException | None], None] | None = None,
) -> DocT | None:
    """Resolve a ref into a document with provenance (§6, §34).

    `ref` may be an `Artifact[SourceRef]`, a `Handle` (from
    `fan_out_sources(..., as_artifacts=True)`), or a plain `SourceRef` — the
    common "search then read" path no longer has to look the artifact up.

    Build the document either with `doc_factory(context, ref, content)` (full
    control) or `doc_type=Model` (the content goes to `text_field`, and the
    model fields are filled from the ref: `locator`, `source_id`, `title`,
    `query_id`, `score`, `metadata`; `field_map={"path": "locator"}` renames).

    The produced document is linked `relation → SourceRef` (default
    `MATERIALIZED_FROM`) in the current effect slot. An existing
    `resolved:{ref_id}` document is reused with `refresh=False`; a missing
    source or a resolve failure is reported to `on_error(ref, exc)` and returns
    `None`, never a crash.
    """
    effects = _active_effects(context)

    if isinstance(ref, SourceRef):
        ref_data = ref
        owner = str(ref.metadata.get("owner_id") or "")
        ref_id = f"ref:{ref.stable_id()}:{owner}" if owner else f"ref:{ref.stable_id()}"
        target: Artifact[SourceRef] | Handle | None = context.get(ref_id)
    elif isinstance(ref, Handle):
        ref_data = cast("SourceRef", ref.data)
        ref_id = ref.id
        target = ref
    else:
        ref_data = ref.data
        ref_id = ref.id
        target = ref

    doc_id = f"resolved:{ref_id}"
    if not refresh:
        existing_doc = context.get(doc_id)
        if existing_doc is not None:
            return cast("DocT", existing_doc.data)

    source = context.resources.get_source(ref_data.source_id)
    if source is None:
        _report(on_error, ref_data, None)
        return None
    try:
        content = await source.resolve(ref_data)
    except Exception as exc:  # noqa: BLE001 — honest no-op, reported to caller
        _report(on_error, ref_data, exc)
        return None

    if target is None:
        target = effects.create(ref_data, id=ref_id)

    if doc_factory is not None:
        doc = doc_factory(context, target, content)
    elif doc_type is not None:
        doc = _doc_from_type(
            doc_type,
            ref_data,
            content,
            text_field=text_field,
            field_map=field_map,
        )
    else:
        raise TypeError("materialize_doc requires doc_factory= or doc_type=")

    handle = effects.create(doc, id=doc_id)
    handle.link(relation, target)
    return doc


__all__ = ["materialize_doc"]
