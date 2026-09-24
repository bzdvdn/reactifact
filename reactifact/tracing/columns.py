"""Configurable trace-table columns: pull a field out of a run's artifacts.

A `TraceColumn` names a column for the traces table and where its value comes
from: which agent's span, which artifact type, reads or writes, and a dotted
path into the artifact's data. A list step takes its first element unless an
explicit index is given, so `sources.title` means `sources[0].title`.

    TraceColumn(label="Q", agent="start", type=Question, field="query")
    TraceColumn(label="Answer", agent="normalize",
                type=FinalResponse, field="answer")
    TraceColumn(label="Top source", agent="search", type=Evidence,
                field="sources.title")

Extraction runs over the artifact JSON persisted with each span. That JSON is
clipped by `RuntimeResources.trace_truncate` (default 1500 chars); a value cut
off mid-JSON cannot be parsed and falls back to the column's `default`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Literal

from .models import ArtifactRef

#: Which side of a span to read the artifact from.
Direction = Literal["read", "write", "any"]

#: How far to look for the artifact: this run alone, or its whole session
#: (useful for a chat, where the question lives in the first run and the final
#: answer in a later one — every row then shows the session's Q/A).
Scope = Literal["run", "session"]


@dataclass(frozen=True)
class TraceColumn:
    """One configurable column in the traces table.

    `agent` / `type` narrow which artifacts are considered (both optional);
    `direction` picks reads, writes or either; `index` selects among the
    matches (default: the last one); `field` is a dotted path into that
    artifact's JSON data (empty = the whole object). `scope="session"` looks
    across every run of the row's session (chronological) instead of just this
    run — so a chat's question (`index=0`) and final answer (`index=-1`) show on
    every row. `default` is shown when nothing matches or the data is
    unparseable.
    """

    label: str
    field: str
    agent: str | None = None
    type: str | type | None = None
    direction: Direction = "any"
    index: int = -1
    scope: Scope = "run"
    default: str = "—"

    def type_name(self) -> str | None:
        if self.type is None:
            return None
        return getattr(self.type, "__name__", str(self.type))


@dataclass(frozen=True)
class SpanArtifacts:
    """The minimum a column needs off a span: its agent + read/write refs."""

    agent: str
    reads: list[ArtifactRef] = dataclass_field(default_factory=list)
    writes: list[ArtifactRef] = dataclass_field(default_factory=list)


def _candidates(
    spans: Sequence[SpanArtifacts], column: TraceColumn
) -> list[ArtifactRef]:
    """Artifacts across `spans` matching the column's agent/type/direction."""
    want = column.type_name()
    found: list[ArtifactRef] = []
    for span in spans:
        if column.agent is not None and span.agent != column.agent:
            continue
        # `Runtime._collect_reads` records the triggering artifact first, then
        # the other inputs — so the trigger (the newest artifact of the run) is
        # first. Reverse reads so it comes *last*, making the default
        # `index=-1` mean "most recent" across reads and writes alike.
        reads = list(reversed(span.reads))
        if column.direction == "write":
            refs = span.writes
        elif column.direction == "read":
            refs = reads
        else:
            refs = [*reads, *span.writes]
        found.extend(ref for ref in refs if want is None or ref.data_type == want)
    return found


def _walk(value: Any, parts: Sequence[str]) -> Any:
    """Descends `value` along `parts`; a list step takes element 0 by default."""
    for part in parts:
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)):
            if part.lstrip("-").isdigit():
                index = int(part)
                value = value[index] if -len(value) <= index < len(value) else None
            elif value:
                first = value[0]
                value = first.get(part) if isinstance(first, dict) else None
            else:
                value = None
        else:
            return None
    return value


def _display(value: Any) -> str | None:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def extract_columns(
    spans: Sequence[SpanArtifacts],
    columns: Sequence[TraceColumn],
    *,
    session_spans: Sequence[SpanArtifacts] | None = None,
) -> dict[str, str]:
    """Resolves every column to a display string for one run.

    `spans` are the run's own spans; `session_spans` (when given) are every span
    of the row's session in chronological order, used by `scope="session"`
    columns. Best-effort by design (a dashboard convenience): a missing
    artifact, an out-of-range index, or JSON clipped by the trace truncation
    limit yields the column's `default` instead of raising.
    """
    out: dict[str, str] = {}
    for column in columns:
        source = session_spans if column.scope == "session" else spans
        refs = _candidates(source if source is not None else spans, column)
        ref: ArtifactRef | None = None
        if refs:
            try:
                ref = refs[column.index]
            except IndexError:
                ref = None
        value: Any = None
        if ref is not None and ref.data:
            try:
                parsed = json.loads(ref.data)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                value = (
                    _walk(parsed, column.field.split(".")) if column.field else parsed
                )
        display = _display(value)
        out[column.label] = display if display is not None else column.default
    return out
