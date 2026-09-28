"""`reactifact eval` — online evaluation over a trace store (§56).

Samples finished runs out of a `TraceStore` (SQLite) and scores them with
`reactifact.eval`, printing the report and (by default) tagging the runs so the
dashboard can surface them:

    python -m reactifact eval traces.db
    python -m reactifact eval traces.db --sample 1.0 --evaluators judge --provider openai:gpt-4o-mini
    python -m reactifact eval traces.db --no-annotate --limit 50 --outcome completed

Because a trace is truncated, the default `--evaluators trace` scores only what
a `RunTrace` carries: the run produced its output (`output_present`) and no
agent span errored (`no_errors`). `--evaluators judge` adds an LLM-as-judge
step (needs `--provider PROVIDER:model`); `all` runs both. To score with
`core_metrics` (which need a full `Context`), use `OnlineEvalConfig` +
`context_source` from a script, not this CLI.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping

from ..eval.evaluators import Evaluator
from ..eval.online import (
    OnlineEvalConfig,
    no_errors,
    online_evaluate,
    output_present,
    trace_source,
)
from ..providers import LLMProvider
from ..tracing.store import TraceStore


def _provider(spec: str) -> LLMProvider:
    """Builds an `LLMProvider` from `PROVIDER[:model]` using the factory registry."""
    from .. import providers

    name, _, model = spec.partition(":")
    kwargs = {"model": model} if model else {}
    factory = getattr(providers, f"{name}_llm", None)
    if factory is None:
        raise SystemExit(
            f"unknown provider {name!r} (try: openai, anthropic, gemini, …)"
        )
    return factory(**kwargs)  # type: ignore[no-any-return]


def _evaluators(kind: str, provider_spec: str | None) -> Mapping[str, Evaluator]:
    evaluators: dict[str, Evaluator] = {}
    if kind in ("trace", "all"):
        evaluators["output_present"] = output_present()
        evaluators["no_errors"] = no_errors()
    if kind in ("judge", "all"):
        if provider_spec is None:
            raise SystemExit("--evaluators judge needs --provider PROVIDER[:model]")
        from ..eval.judge import judge_relevance

        evaluators["relevance"] = judge_relevance(_provider(provider_spec))
    return evaluators


def cmd_eval(args: argparse.Namespace) -> int:
    store = TraceStore(args.store)
    config = OnlineEvalConfig(
        evaluators=_evaluators(args.evaluators, args.provider),
        sample_rate=args.sample,
        strategy=args.strategy,
        limit=args.limit,
        session_id=args.session,
        outcome=args.outcome,
        tags=args.tags,
        source=trace_source(),
        annotate=not args.no_annotate,
        tag=args.tag,
        failure_tag=args.failure_tag,
    )
    report = asyncio.run(online_evaluate(store, config))
    print(report.render())
    if report.sampled == 0:
        print("\n(no runs matched — check the store path and filters)")
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("eval", help="online-evaluate runs from a trace store")
    parser.add_argument("store", help="path to a trace store (traces.db)")
    parser.add_argument(
        "--sample",
        type=float,
        default=0.1,
        help="fraction of matching runs to score (0..1, default 0.1)",
    )
    parser.add_argument(
        "--strategy",
        choices=["random", "head"],
        default="random",
        help="sample randomly (seeded) or take the newest runs",
    )
    parser.add_argument("--limit", type=int, default=100, help="max runs to consider")
    parser.add_argument("--session", default=None, help="only this session id")
    parser.add_argument("--outcome", default=None, help="only this run outcome")
    parser.add_argument(
        "--tags", nargs="*", default=None, help="only runs carrying these tags"
    )
    parser.add_argument(
        "--evaluators",
        choices=["trace", "judge", "all"],
        default="trace",
        help="trace (structure/outputs), judge (LLM), or all",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="PROVIDER[:model] for the judge, e.g. openai:gpt-4o-mini",
    )
    parser.add_argument("--tag", default="eval", help="tag for passing runs")
    parser.add_argument(
        "--failure-tag", default="eval:failed", help="tag for failing runs"
    )
    parser.add_argument(
        "--no-annotate", action="store_true", help="do not write tags back"
    )
    parser.set_defaults(func=cmd_eval)
