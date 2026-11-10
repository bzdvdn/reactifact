"""The fintech audit pipeline: retrieve → materialize → compute → verify → answer.

The figures (`Spend`, `Variance`) are computed in plain Python from a
materialized `Table` — the model never produces a number (§67). Every derived
artifact links to what it was derived from, so `reactifact.audit.build_report`
can walk the chain to an auditable record.
"""

from __future__ import annotations

from reactifact import Artifact, Consume, Context, Effects, step
from reactifact.recipes import fan_out_sources
from reactifact.sources import SourceRef

from examples.fintech_audit.models import (
    AuditAnswer,
    Policy,
    Question,
    Spend,
    Table,
    Variance,
)

#: Variance above this fraction of budget requires CFO approval (see policy.md).
POLICY_THRESHOLD = 0.10


@step(produces=SourceRef, consumes=Question)
async def search_sources(context: Context, question: Artifact[Question]) -> None:
    await fan_out_sources(context, question.data.text, owner_id=question.id, limit=5)


@step(produces=(Table, Policy), consumes=SourceRef)
async def materialize(
    context: Context, ref: Artifact[SourceRef], effects: Effects
) -> None:
    """Resolves a `SourceRef` into a `Table` (CSV) or a `Policy` (Markdown)."""
    source = context.resources.get_source(ref.data.source_id)
    if source is None:
        return None
    try:
        content = await source.resolve(ref.data)
    except Exception:  # a missing/unreadable source is a None, not a crash
        return None

    if ref.data.metadata.get("structured") and isinstance(content, dict):
        document: Table | Policy = Table(
            columns=list(content.get("columns", [])),
            rows=list(content.get("rows", [])),
            locator=ref.data.locator,
        )
    else:
        document = Policy(text=str(content), locator=ref.data.locator)
    effects.create(document, id=f"doc:{ref.data.locator}").link(
        "materialized_from", ref
    )
    return None


def _cell(table: Table, row: list[str], column: str) -> str | None:
    try:
        index = table.columns.index(column)
    except ValueError:
        return None
    return row[index] if index < len(row) else None


def _is_budget_table(table: Artifact[Table]) -> bool:
    return table.data.locator.endswith("budget.csv")


def _budget_for(table: Table, category: str) -> float | None:
    for row in table.rows:
        if _cell(table, row, "category") == category:
            value = _cell(table, row, "budget_usd")
            return float(value) if value is not None else None
    return None


@step(produces=Spend, consumes=(Table, Consume(Question, wakes=False)))
async def compute_spend(
    table: Artifact[Table],
    question: Artifact[Question],
    effects: Effects,
) -> None:
    """Sums `cloud` spend by month — deterministic arithmetic, not a model."""
    if not table.data.locator.endswith("transactions.csv"):
        return None
    by_month: dict[str, float] = {}
    total = 0.0
    for row in table.data.rows:
        if _cell(table.data, row, "category") != "cloud":
            continue
        amount = _cell(table.data, row, "amount_usd")
        month = _cell(table.data, row, "month")
        if amount is None or month is None:
            continue
        total += float(amount)
        by_month[month] = by_month.get(month, 0.0) + float(amount)

    handle = effects.create_once_from(
        question, Spend(category="cloud", total=total, by_month=by_month)
    )
    if handle is None:
        return None
    handle.link("calculated_from", table)
    return None


@step(
    produces=Variance,
    consumes=(
        Spend,
        Consume(Question, wakes=False),
        Consume(Table, condition=_is_budget_table, wakes=False),
    ),
)
async def compute_variance(
    spend: Artifact[Spend],
    question: Artifact[Question],
    budget_table: Artifact[Table],
    effects: Effects,
) -> None:
    """Actual vs budget, and whether the variance crosses the policy line."""
    budget = _budget_for(budget_table.data, spend.data.category)
    if budget is None:
        return None

    actual = spend.data.total
    pct = (actual - budget) / budget if budget else 0.0
    handle = effects.create_once_from(
        question,
        Variance(
            category=spend.data.category,
            actual=actual,
            budget=budget,
            pct=pct,
            threshold=POLICY_THRESHOLD,
            within_policy=abs(pct) <= POLICY_THRESHOLD,
        ),
    )
    if handle is None:
        return None
    handle.link("calculated_from", spend)
    handle.link("calculated_from", budget_table)
    return None


def _citations(context: Context, variance: Artifact[Variance]) -> list[str]:
    """Every source locator in the variance's provenance chain (all edges)."""
    locators: list[str] = []
    visited = {variance.id}
    queue = [variance.id]
    while queue:
        current = queue.pop(0)
        for rel in context.relations(source_id=current):
            artifact = context.get(rel.target_id)
            if artifact is None:
                continue
            locator = getattr(artifact.data, "locator", "")
            if isinstance(locator, str) and locator and locator not in locators:
                locators.append(locator)
            if rel.target_id not in visited:
                visited.add(rel.target_id)
                queue.append(rel.target_id)
    policy = context.latest(Policy)
    if policy is not None and policy.data.locator not in locators:
        locators.append(policy.data.locator)
    return locators


@step(produces=AuditAnswer, consumes=(Variance, Consume(Question, wakes=False)))
async def answer(
    context: Context,
    variance: Artifact[Variance],
    question: Artifact[Question],
    effects: Effects,
) -> None:
    """States the computed figures in prose, citing the sources it rests on."""
    v = variance.data
    verdict = "exceeds" if not v.within_policy else "is within"
    text = (
        f"Q2 {v.category} spend was ${v.actual:,.0f} against a "
        f"${v.budget:,.0f} budget ({v.pct:+.1%}) — {verdict} the "
        f"{v.threshold:.0%} policy threshold."
    )
    if not v.within_policy:
        text += " CFO approval is required before the quarter is closed."

    handle = effects.create_once_from(
        question,
        AuditAnswer(
            text=text,
            figures={"actual": v.actual, "budget": v.budget, "pct": v.pct},
            citations=_citations(context, variance),
        ),
    )
    if handle is None:
        return None
    handle.link("supported_by", variance)
    for artifact in context.related(variance.id, "calculated_from"):
        handle.link("supported_by", artifact)
    policy = context.latest(Policy)
    if policy is not None:
        handle.link("supported_by", policy)
    return None


__all__ = [
    "POLICY_THRESHOLD",
    "answer",
    "compute_spend",
    "compute_variance",
    "materialize",
    "search_sources",
]
