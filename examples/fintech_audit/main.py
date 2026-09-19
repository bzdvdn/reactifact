"""fintech_audit — an auditable answer: deterministic figures, provenance, hashes.

A finance question ("what is the Q2 cloud spend variance, and does policy
require approval?") answered by reacting to sources: a transactions CSV, a
budget CSV, and a policy document. The numbers are computed in plain Python —
the model is never the source of truth (§67) — and the answer links back to the
artifacts it rests on, so `reactifact.audit` renders a verifiable report.

The whole run is deterministic (stable ids, no timestamps in the hash), so
re-running it yields the same `context_hash` — the reproducibility check a
reviewer or a `reactifact replay --verify <hash>` would run.

No API key needed: nothing here calls a model.

Run:  .venv/bin/python -m examples.fintech_audit.main
"""

from __future__ import annotations

import asyncio
import sys
from functools import partial
from pathlib import Path

if __package__ in (None, ""):  # run as a script — add the repo root to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reactifact import Budget, Context, Runtime, RuntimeResources
from reactifact.audit import build_report, context_hash, report_to_markdown
from reactifact.recipes import keyword_score
from reactifact.sources import CSVSource, FileSystemSource

from examples.fintech_audit.agents import AGENTS
from examples.fintech_audit.models import AuditAnswer, Question, Spend, Variance

DATA = Path(__file__).parent / "data"
QUESTION = "What is the Q2 cloud spend variance, and does policy require approval?"

# stop-word- and plural-aware matching, so "require" finds "requires"
_SCORE = partial(keyword_score, fold_plurals=True)


def build_resources() -> RuntimeResources:
    return RuntimeResources(
        sources={
            "docs": FileSystemSource(
                str(DATA), source_id="docs", extensions=(".md",), scorer=_SCORE
            ),
            "tables": CSVSource(str(DATA), source_id="tables", scorer=_SCORE),
        }
    )


async def run_pipeline() -> Context:
    """One full run. Called twice in `main` to prove the state is reproducible."""
    context = Context(resources=build_resources())
    runtime = Runtime(context, agents=AGENTS, budget=Budget(max_runs=50))
    context.create(Question(text=QUESTION), id="q2")
    await runtime.arun()
    return context


async def main() -> int:
    context = await run_pipeline()
    spend = context.latest(Spend)
    variance = context.latest(Variance)
    answer = context.latest(AuditAnswer)
    assert spend is not None and variance is not None and answer is not None

    print("computed figures (plain Python — not the model):")
    print(f"  cloud spend: ${spend.data.total:,.0f}  by month: {spend.data.by_month}")
    print(
        f"  variance vs budget: {variance.data.pct:+.1%} "
        f"(threshold {variance.data.threshold:.0%}, "
        f"within policy: {variance.data.within_policy})"
    )
    print(f"\nanswer: {answer.data.text}")
    print(f"citations: {answer.data.citations}")

    report = build_report(context, answer)
    print("\n" + report_to_markdown(report))
    digest = context_hash(context)
    print(f"context sha256: {digest}")
    print(
        "verify anytime:  reactifact replay <sessions.sqlite3> "
        f"--session <id> --verify {digest}"
    )

    print(">>> re-running the same pipeline to prove reproducibility…")
    second = await run_pipeline()
    if context_hash(second) != digest:
        print("[FAILED] the second run hashed differently")
        return 1
    print(
        "[confirmed] the second run hashes identically — the answer is "
        "reproducible from its sources."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
