"""knowledge calc — deterministic aggregation instead of hallucination (§67)."""

from __future__ import annotations

import re

from reactifact import Produce, ProduceCall

from ..models import Calculation, ResearchTurn, Spreadsheet
from .common import interesting_column_re

_CALC_INTENT_RE = re.compile(
    r"(sum|total|average|avg|mean|how much[^\n]{0,20}(cost|spend)|"
    r"max|maximum|min|minimum)",
    re.IGNORECASE,
)


def aggregate_intent(question: str) -> str | None:
    """Which aggregation is requested (sum/mean/max/min), or None (§67)."""
    if not question:
        return None
    matched = _CALC_INTENT_RE.search(question)
    if matched is None:
        return None
    token = matched.group(0).casefold()
    if "sum" in token or "total" in token or "cost" in token or "spend" in token:
        return "sum"
    if "average" in token or "avg" in token or "mean" in token:
        return "mean"
    if "max" in token:
        return "max"
    if "min" in token:
        return "min"
    return None


def numeric_column(sheet: Spreadsheet) -> tuple[str | None, list[float]]:
    """The most specific «cost/usage» column; otherwise the first numeric one."""
    candidates = sheet.columns
    matched = [col for col in candidates if interesting_column_re().search(col)]
    chosen = max(matched, key=len, default=None)
    if chosen is None:
        chosen = candidates[0] if candidates else ""
    try:
        idx = sheet.columns.index(chosen)
    except ValueError:
        return None, []
    values: list[float] = []
    for row in sheet.rows:
        if idx >= len(row):
            continue
        raw = row[idx].strip()
        try:
            values.append(float(raw.replace(",", ".")))
        except ValueError:
            continue
    return chosen, values


class CalculateAggregate(Produce[Calculation]):
    """Calculation: computes over the spreadsheet if asked for an aggregate (§29, §67).

    Deterministic arithmetic (sum/mean/max/min over the relevant numeric column).
    Provenance: Calculation —derived_from→ Spreadsheet.
    """

    artifact_type = Calculation

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        sheet_art = call.trigger
        if sheet_art is None or not isinstance(sheet_art.data, Spreadsheet):
            return None
        sheet = sheet_art.data
        question = next(
            (
                t.data.text
                for t in context.list_artifacts(ResearchTurn)
                if t.data.query_id == sheet.query_id
            ),
            "",
        )
        func = aggregate_intent(question)
        if func is None:
            return None
        column, values = numeric_column(sheet)
        if column is None or not values:
            return None

        if func == "sum":
            value = sum(values)
            description = f"Sum over column «{column}»"
        elif func == "mean":
            value = sum(values) / len(values)
            description = f"Mean of column «{column}»"
        elif func == "max":
            value = max(values)
            description = f"Maximum of column «{column}»"
        else:
            value = min(values)
            description = f"Minimum of column «{column}»"
        description += f" ({len(values)} rows, {sheet.source_id}:{sheet.path})"
        value = round(value, 2) if isinstance(value, float) else value
        context.announce(
            f"Computing: {description} = {value}",
            kind="status",
            path=sheet.path,
        )
        calc_id = f"calc:{sheet.query_id}:{sheet_art.id}"
        calc = self.effects.create(
            Calculation(
                query_id=sheet.query_id,
                description=description,
                value=value,
                column=column,
                rows=len(values),
            ),
            id=calc_id,
        )
        calc.link("derived_from", sheet_art)
        return None
