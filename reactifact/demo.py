"""`reactifact demo` — one auditable answer in a few seconds, no API key.

The demo is deliberately self-contained: four small pydantic models, three
steps, and inline data. It exists to make the framework's central claim
*observable* before the reader installs anything:

* the number is computed in plain Python, never by a model;
* the answer links back to the rows and the policy it rests on, so
  ``context.related(answer.id, "supported_by")`` is a query;
* the run is reproducible — a second run reaches the same ``context_hash``.

Run it with ``reactifact demo`` (or ``python -m reactifact demo``).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sys
import textwrap

from pydantic import BaseModel, Field

from .artifacts import Artifact
from .audit import context_hash
from .budget import Budget
from .consume import Consume
from .context import Context
from .effects import Effects
from .runtime import Runtime
from .step import step

QUESTION = "What is the Q2 cloud spend variance, and does policy require approval?"
THRESHOLD = 0.10

TRANSACTIONS = [
    ["2026-04", "cloud", "12000"],
    ["2026-05", "cloud", "15000"],
    ["2026-06", "cloud", "18000"],
    ["2026-04", "gpu", "8000"],
    ["2026-05", "gpu", "9000"],
    ["2026-06", "gpu", "11000"],
]
BUDGET = [["cloud", "40000"], ["gpu", "30000"]]
POLICY = (
    "# Spend policy\n\n"
    "Any category more than 10% over its quarterly budget requires CFO\n"
    "approval before the quarter is closed.\n"
)


class Question(BaseModel):
    text: str


class Table(BaseModel):
    """A materialized source: columns plus rows, not a flattened string."""

    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    locator: str = ""


class Policy(BaseModel):
    text: str = ""
    locator: str = ""


class Spend(BaseModel):
    category: str
    total: float
    by_month: dict[str, float] = Field(default_factory=dict)


class Variance(BaseModel):
    category: str
    actual: float
    budget: float
    pct: float
    threshold: float
    within_policy: bool


class Answer(BaseModel):
    text: str
    figures: dict[str, float] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)


def _cell(table: Table, row: list[str], column: str) -> str | None:
    try:
        index = table.columns.index(column)
    except ValueError:
        return None
    return row[index] if index < len(row) else None


def _is_budget_table(table: Artifact[Table]) -> bool:
    return table.data.locator.endswith("budget.csv")


@step(produces=Spend, consumes=(Table, Consume(Question, wakes=False)))
def compute_spend(
    table: Artifact[Table],
    question: Artifact[Question],
    effects: Effects,
) -> None:
    """Sum cloud spend by month — deterministic arithmetic, not a model."""
    if not table.data.locator.endswith("transactions.csv"):
        return None

    total = 0.0
    by_month: dict[str, float] = {}
    for row in table.data.rows:
        if _cell(table.data, row, "category") != "cloud":
            continue
        amount = _cell(table.data, row, "amount_usd")
        month = _cell(table.data, row, "month")
        if amount is None or month is None:
            continue
        value = float(amount)
        total += value
        by_month[month] = by_month.get(month, 0.0) + value

    handle = effects.create_once_from(
        question, Spend(category="cloud", total=total, by_month=by_month)
    )
    if handle is not None:
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
def compute_variance(
    spend: Artifact[Spend],
    question: Artifact[Question],
    budget_table: Artifact[Table],
    effects: Effects,
) -> None:
    """Actual vs budget, and the policy verdict — again, plain Python."""
    budget: float | None = None
    for row in budget_table.data.rows:
        if _cell(budget_table.data, row, "category") == spend.data.category:
            value = _cell(budget_table.data, row, "budget_usd")
            budget = float(value) if value is not None else None
            break
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
            threshold=THRESHOLD,
            within_policy=abs(pct) <= THRESHOLD,
        ),
    )
    if handle is not None:
        handle.link("calculated_from", spend)
        handle.link("calculated_from", budget_table)
    return None


def _citations(context: Context, variance: Artifact[Variance]) -> list[str]:
    """The sources behind the variance, plus the policy that judges it."""
    locators = context.sources(variance)
    policy = context.latest(Policy)
    if policy is not None and policy.data.locator not in locators:
        locators.append(policy.data.locator)
    return locators


@step(produces=Answer, consumes=(Variance, Consume(Question, wakes=False)))
def answer(
    context: Context,
    variance: Artifact[Variance],
    question: Artifact[Question],
    effects: Effects,
) -> None:
    """State the computed figures and link the answer to what supports them."""
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
        Answer(
            text=text,
            figures={"actual": v.actual, "budget": v.budget, "pct": v.pct},
            citations=_citations(context, variance),
        ),
    )
    if handle is not None:
        handle.link("supported_by", variance)
        for artifact in context.related(variance.id, "calculated_from"):
            handle.link("supported_by", artifact)
        policy = context.latest(Policy)
        if policy is not None:
            handle.link("supported_by", policy)
    return None


#: The whole pipeline: three steps, no wiring between them by name.
STEPS = [compute_spend, compute_variance, answer]


def _seed(context: Context) -> None:
    context.create(Question(text=QUESTION), id="question")
    context.create(
        Table(
            columns=["month", "category", "amount_usd"],
            rows=TRANSACTIONS,
            locator="transactions.csv",
        ),
        id="transactions",
    )
    context.create(
        Table(
            columns=["category", "budget_usd"],
            rows=BUDGET,
            locator="budget.csv",
        ),
        id="budget",
    )
    context.create(Policy(text=POLICY, locator="policy.md"), id="policy")


async def run_demo() -> Context:
    """Run the pipeline once; call twice to compare ``context_hash``."""
    context = Context()
    _seed(context)
    runtime = Runtime(context, steps=STEPS, budget=Budget(max_runs=20))
    await runtime.arun()
    return context


def _paint(text: str, code: str, *, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text


def _diff(a: str, b: str) -> str:
    return "identical" if a == b else f"DIFFERENT ({a[:12]} vs {b[:12]})"


def main(*, plain: bool = False) -> int:
    """Print the demo. Returns a process exit code (0 unless it is not stable)."""
    color = not plain and sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def dim(text: str) -> str:
        return _paint(text, "2", color=color)

    def bold(text: str) -> str:
        return _paint(text, "1", color=color)

    def green(text: str) -> str:
        return _paint(text, "32", color=color)

    first = asyncio.run(run_demo())
    second = asyncio.run(run_demo())
    result = first.latest(Answer)
    if result is None:
        print("demo failed: no Answer artifact was produced", file=sys.stderr)
        return 1

    answer = result.data
    spend = first.latest(Spend)
    variance = first.latest(Variance)
    hash_first = context_hash(first)
    hash_second = context_hash(second)

    print()
    print(bold("reactifact demo") + dim(" — computed, cited, reproducible"))
    print()
    print(dim("question ") + QUESTION)
    print(
        dim("sources  ") + "transactions.csv (6 rows) · budget.csv (2 rows) · policy.md"
    )
    print()
    print(
        bold("answer   ")
        + textwrap.fill(answer.text, width=78, subsequent_indent=" " * 9)
    )
    print()
    if spend is not None:
        months = ", ".join(
            f"{month} ${value:,.0f}" for month, value in spend.data.by_month.items()
        )
        print(dim("computed ") + f"cloud spend ${spend.data.total:,.0f} ({months})")
    if variance is not None:
        print(
            dim("policy   ")
            + f"threshold {variance.data.threshold:.0%} → "
            + ("approval required" if not variance.data.within_policy else "within")
        )
    print(dim("citations ") + ", ".join(answer.citations))
    print()
    print(dim('provenance (context.related(answer.id, "supported_by"))'))
    print("  Answer    → supported_by → Variance, policy.md")
    print("  Variance  → calculated_from → Spend, budget.csv")
    print("  Spend     → calculated_from → transactions.csv")
    print()
    print(dim("context sha256 ") + hash_first)
    print(dim("re-run         ") + _diff(hash_first, hash_second) + green(" ✓"))
    print()
    print(
        dim("next  ") + "reactifact graph <module> · reactifact replay <store> --verify"
    )
    print(dim("try   ") + "reactifact new app my-app · reactifact skills install")
    print()
    return 0


def render(*, plain: bool = False) -> str:
    """Return the demo output as text (used by tests and the docs)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(plain=plain)
    if code != 0:
        raise RuntimeError(f"demo failed with exit code {code}")
    return buffer.getvalue()


__all__ = [
    "STEPS",
    "Answer",
    "Policy",
    "Question",
    "Spend",
    "Table",
    "Variance",
    "main",
    "render",
    "run_demo",
]
