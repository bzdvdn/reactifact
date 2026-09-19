"""Artifacts for the fintech audit example.

The audited truth is structured and deterministic: `Spend`/`Variance` are
computed in plain Python from a materialized `Table`, never by a model. The
answer is prose *about* those figures, and every artifact links back to what it
was derived from, so `reactifact.audit` can walk the chain.
"""

from pydantic import BaseModel, Field


class Question(BaseModel):
    text: str


class Table(BaseModel):
    """A materialized structured source (a CSV): schema preserved, not flattened."""

    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    locator: str = ""


class Policy(BaseModel):
    """A materialized policy document (Markdown/text)."""

    text: str = ""
    locator: str = ""


class Spend(BaseModel):
    """Deterministic spend total for one category, from `transactions.csv`."""

    category: str
    total: float
    by_month: dict[str, float] = Field(default_factory=dict)
    currency: str = "USD"


class Variance(BaseModel):
    """Deterministic variance of actual vs budget, and the policy verdict."""

    category: str
    actual: float
    budget: float
    pct: float
    threshold: float
    within_policy: bool


class AuditAnswer(BaseModel):
    """The prose answer, plus the exact figures it states and its citations."""

    text: str
    figures: dict[str, float] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)
