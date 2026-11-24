"""`reactifact demo` — the no-key on-ramp must stay deterministic and cited."""

from __future__ import annotations

import asyncio

from reactifact.audit import context_hash
from reactifact.cli import main
from reactifact.demo import Answer, render, run_demo


def run(coro):
    return asyncio.run(coro)


def test_demo_runs_are_reproducible():
    first = run(run_demo())
    second = run(run_demo())
    assert context_hash(first) == context_hash(second)


def test_demo_computes_in_python_and_cites_sources():
    context = run(run_demo())
    answer = context.latest(Answer)
    assert answer is not None
    assert answer.data.figures == {"actual": 45000.0, "budget": 40000.0, "pct": 0.125}
    assert "CFO approval is required" in answer.data.text
    assert set(answer.data.citations) == {
        "transactions.csv",
        "budget.csv",
        "policy.md",
    }
    assert context.related(answer.id, "supported_by")


def test_demo_output_is_plain_when_asked(capsys):
    assert main(["demo", "--plain"]) == 0
    out = capsys.readouterr().out
    assert "\033[" not in out
    assert "$45,000" in out
    assert "context sha256" in out
    assert "identical" in out


def test_render_returns_text():
    text = render(plain=True)
    assert "reactifact demo" in text
    assert "supported_by" in text
