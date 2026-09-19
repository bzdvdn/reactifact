"""The fintech_audit example: deterministic figures + an auditable answer."""

import asyncio

from examples.fintech_audit.main import run_pipeline
from examples.fintech_audit.models import AuditAnswer, Spend, Variance
from reactifact.audit import build_report, context_hash


def test_fintech_audit_computes_figures_and_walks_provenance():
    context = asyncio.run(run_pipeline())

    spend = context.latest(Spend)
    variance = context.latest(Variance)
    answer = context.latest(AuditAnswer)
    assert spend is not None and variance is not None and answer is not None

    assert spend.data.total == 45000.0
    assert spend.data.by_month == {
        "2026-04": 12000.0,
        "2026-05": 15000.0,
        "2026-06": 18000.0,
    }
    assert variance.data.budget == 40000.0
    assert abs(variance.data.pct - 0.125) < 1e-9
    assert variance.data.within_policy is False
    assert answer.data.figures == {"actual": 45000.0, "budget": 40000.0, "pct": 0.125}

    report = build_report(context, answer)
    assert report.answer.data_type == "AuditAnswer"
    assert {"transactions.csv", "budget.csv", "policy.md"} <= set(report.sources)
    assert report.context_sha256 == context_hash(context)
    assert any(rel[1] == "calculated_from" for rel in report.relations)
    assert any(rel[1] == "materialized_from" for rel in report.relations)


def test_fintech_audit_is_reproducible():
    first = asyncio.run(run_pipeline())
    second = asyncio.run(run_pipeline())
    assert context_hash(first) == context_hash(second)
