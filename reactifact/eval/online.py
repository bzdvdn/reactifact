"""Online evaluation — score *live* runs from a trace store (§56).

Offline evaluation (`reactifact.eval.evaluate`) runs a pipeline over a curated
`Dataset`. Online evaluation is its production counterpart: sample finished
runs out of a `TraceReader`, score them with the same `Evaluator`s (including
`llm_judge`), and write the verdicts back onto the run as tags — the reviewer
annotation channel the dashboard already reads — plus metrics.

The catch is that a trace is a *summary*: artifact data and LLM messages are
truncated for storage (`RuntimeResources.trace_truncate`), so not every metric
can be computed from a `RunTrace` alone. Two sources cover the range:

- `trace_source()` (default, cheap): scores structure — the run's typed path
  (`trajectory_match` over spans), which agents and artifact types ran, and any
  text still present — from the `RunTrace` itself. A metric that needs data the
  trace no longer carries returns `None` and is reported as skipped.
- `context_source(run_fn)`: the app rebuilds the full `Context` for a run id,
  so text-dependent metrics and judges see complete data. Use it when a run is
  persisted (session store) and exactness matters.

    from reactifact.eval import OnlineEvalConfig, OnlineEvaluator, trajectory_match
    from reactifact.eval.online import trace_source

    config = OnlineEvalConfig(
        evaluators={"path": trajectory_match("superset", steps="agents")},
        sample_rate=0.1,
        tag="eval",
    )
    evaluator = OnlineEvaluator(store, config, metrics=metrics)
    await evaluator.run_once()          # score one batch of sampled runs
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..context import Context
from ..tracing.models import ArtifactRef, RunTrace
from ..tracing.store import TraceStoreProtocol
from .dataset import Example
from .evaluators import EvalInput, Evaluator
from .runner import _as_feedbacks, _evaluator_list, _run_evaluator, default_outputs
from .scoring import EvalReport, EvalResult, Metric

logger = logging.getLogger(__name__)

#: Rebuilds a run's full `Context` from its trace id (for exact text scoring).
RunFn = Callable[[str], Context | Awaitable[Context]]


class TraceSource(Protocol):
    """Turns one `RunTrace` into an `EvalInput` for the evaluators."""

    async def eval_input(self, run: RunTrace) -> EvalInput: ...


def _outputs_from_trace(run: RunTrace) -> dict[str, Any]:
    """Reads outputs off a trace's writes, `Answer`-first (mirrors `default_outputs`).

    Falls back to grouping every written artifact by type name when no
    `Answer`-shaped write exists, so most metrics have something to read.
    """
    writes: list[ArtifactRef] = []
    for span in run.spans:
        writes.extend(span.writes)
    answers = [ref for ref in writes if ref.data_type == "Answer"]
    if answers:
        return {"answer": answers[-1].data or ""}
    by_type: dict[str, list[str]] = {}
    for ref in writes:
        by_type.setdefault(ref.data_type or "artifact", []).append(ref.data or "")
    return {"artifacts": by_type}


class _TraceSource:
    """`TraceSource` that scores a `RunTrace` as-is (no rehydration)."""

    async def eval_input(self, run: RunTrace) -> EvalInput:
        return EvalInput(
            example=Example(inputs={}, metadata={"trace_id": run.id}),
            outputs=_outputs_from_trace(run),
            trace=run,
        )


class _ContextSource:
    """`TraceSource` that rebuilds the `Context` for exact text scoring."""

    def __init__(self, run_fn: RunFn) -> None:
        self._run_fn = run_fn

    async def eval_input(self, run: RunTrace) -> EvalInput:
        # A sync run_fn (e.g. one that calls `asyncio.run` itself) must run off
        # the loop `online_evaluate` owns — mirror `runner._evaluate_one`.
        if asyncio.iscoroutinefunction(self._run_fn):
            built = self._run_fn(run.id)
        else:
            sync_fn: Callable[[str], Any] = self._run_fn
            built = await asyncio.to_thread(sync_fn, run.id)
        context = built if isinstance(built, Context) else await built
        return EvalInput(
            example=Example(inputs={}, metadata={"trace_id": run.id}),
            outputs=default_outputs(context),
            context=context,
            trace=run,
        )


def trace_source() -> TraceSource:
    """The default source: score the `RunTrace` directly (cheap, structure-first)."""
    return _TraceSource()


def context_source(run_fn: RunFn) -> TraceSource:
    """Score a rehydrated `Context` (exact text, at the cost of a rebuild)."""
    return _ContextSource(run_fn)


def output_present(key: str = "answer") -> Evaluator:
    """A trace-level evaluator: the run's outputs have a non-empty `key`.

    Works without a `Context` (the whole point of `trace_source`), so it is the
    natural default for online scoring; use a `from_metric` evaluator with
    `context_source` when a metric needs the full artifact.
    """

    def evaluate(eval_input: EvalInput) -> float:
        value = eval_input.outputs.get(key)
        if isinstance(value, str):
            return 1.0 if value.strip() else 0.0
        return 1.0 if value else 0.0

    evaluate.__name__ = f"output_present[{key}]"
    return evaluate


def no_errors() -> Evaluator:
    """A trace-level evaluator: no agent span in the run failed."""

    def evaluate(eval_input: EvalInput) -> float | None:
        trace = eval_input.trace
        if trace is None:
            return None
        return 0.0 if any(span.error for span in trace.spans) else 1.0

    evaluate.__name__ = "no_errors"
    return evaluate


@dataclass
class OnlineEvalConfig:
    """What to sample from a store and how to score it.

    `strategy="random"` (default) samples `sample_rate` of the matching runs
    with a seeded RNG (reproducible); `"head"` takes the newest `limit`. A run
    that fails any metric (or any evaluator) gets `failure_tag`, so the
    dashboard has a ready "needs a look" bucket.
    """

    evaluators: Mapping[str, Evaluator] | Sequence[Evaluator]
    sample_rate: float = 0.1
    strategy: str = "random"  # "random" | "head"
    seed: int | None = 0
    limit: int = 100
    session_id: str | None = None
    outcome: str | None = None
    tags: list[str] | None = None
    source: TraceSource | None = None
    #: Optional ground truth per run (e.g. an expected trajectory), attached to
    #: the `EvalInput.example.reference_outputs` before scoring.
    reference_fn: Callable[[RunTrace], Mapping[str, Any] | None] | None = None
    annotate: bool = True
    tag: str = "eval"
    failure_tag: str = "eval:failed"
    passed_threshold: float = 1.0


@dataclass
class OnlineReport:
    """The outcome of one online-evaluation batch."""

    report: EvalReport = field(default_factory=EvalReport)
    sampled: int = 0
    evaluated: int = 0
    skipped: int = 0
    annotated: int = 0
    failed: int = 0

    def overall(self) -> float:
        return self.report.overall()

    def aggregate(self) -> dict[str, float]:
        return self.report.aggregate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sampled": self.sampled,
            "evaluated": self.evaluated,
            "skipped": self.skipped,
            "annotated": self.annotated,
            "failed": self.failed,
            "overall": round(self.overall(), 4),
            "aggregate": self.aggregate(),
            "results": self.report.to_dict()["cases"],
        }

    def render(self) -> str:
        header = (
            f"online eval · {self.evaluated}/{self.sampled} scored · "
            f"{self.skipped} skipped · {self.failed} failed · "
            f"overall {self.overall():.3f}"
        )
        return header + "\n\n" + self.report.render()


def _sample(
    items: list[dict[str, Any]], config: OnlineEvalConfig
) -> list[dict[str, Any]]:
    """Selects which queried runs to score (deterministic under a `seed`)."""
    if config.strategy == "head":
        return items
    if config.sample_rate >= 1.0:
        return items
    rng = random.Random(config.seed)
    return [item for item in items if rng.random() < config.sample_rate]


async def online_evaluate(
    store: TraceStoreProtocol,
    config: OnlineEvalConfig,
    *,
    metrics: Any | None = None,
) -> OnlineReport:
    """Samples runs from `store`, scores them, annotates and aggregates.

    `metrics` is any `reactifact.metrics.Metrics` (a `MetricsSink`): each metric
    is recorded as `reactifact_online_eval_score{metric}` (histogram) and each
    run as `reactifact_online_eval_runs_total{metric,result}`.
    """
    query = await store.query(
        session_id=config.session_id,
        outcome=config.outcome,
        tags=config.tags,
        limit=config.limit,
    )
    items = list(query.get("items", []))
    sampled_items = _sample(items, config)

    evaluators = _evaluator_list(config.evaluators)
    source = config.source or trace_source()
    report = OnlineReport(sampled=len(sampled_items))
    pass_ids: list[str] = []
    fail_ids: list[str] = []

    for item in sampled_items:
        run = await store.get(item["id"])
        if run is None:
            continue
        eval_input = await source.eval_input(run)
        if config.reference_fn is not None:
            eval_input.example.reference_outputs = config.reference_fn(run)
        result = EvalResult(case=run.id)
        for name, evaluator in evaluators:
            value = await _run_evaluator(evaluator, eval_input)
            feedbacks, skipped = _as_feedbacks(value, name)
            if skipped:
                result.skipped.append(name)
            for feedback in feedbacks:
                result.metrics.append(
                    Metric(
                        name=feedback.key,
                        score=round(max(0.0, min(1.0, feedback.score)), 4),
                        note=feedback.comment,
                    )
                )
        report.report.results.append(result)
        if result.metrics:
            report.evaluated += 1
        if result.skipped and not result.metrics:
            report.skipped += 1
        if metrics is not None:
            for metric in result.metrics:
                outcome = "pass" if metric.score >= config.passed_threshold else "fail"
                metrics.observe(
                    "reactifact_online_eval_score", metric.score, metric=metric.name
                )
                metrics.increment(
                    "reactifact_online_eval_runs_total",
                    metric=metric.name,
                    result=outcome,
                )
        if result.overall() >= config.passed_threshold:
            pass_ids.append(run.id)
        else:
            fail_ids.append(run.id)
            report.failed += 1

    if config.annotate:
        if pass_ids:
            report.annotated += await store.tag_runs(
                pass_ids, config.tag, note="online eval passed"
            )
        if fail_ids:
            report.annotated += await store.tag_runs(
                fail_ids,
                config.failure_tag,
                note=f"online eval < {config.passed_threshold}",
            )
    return report


class OnlineEvaluator:
    """Runs `online_evaluate` on a schedule against a live trace store.

    `run_once()` scores one batch (guarded by a lock, so overlapping calls are
    serialized); `run_forever(interval_seconds)` repeats it — drive it from a
    FastAPI lifespan task or a `asyncio.create_task`. Failure to score never
    crashes the loop; it is logged. `max_batches` bounds `run_forever` for
    tests and one-shot jobs.
    """

    def __init__(
        self,
        store: TraceStoreProtocol,
        config: OnlineEvalConfig,
        *,
        metrics: Any | None = None,
        on_report: Callable[[OnlineReport], None] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.metrics = metrics
        self.on_report = on_report
        self.last_report: OnlineReport | None = None
        self._lock = asyncio.Lock()

    async def run_once(self) -> OnlineReport:
        async with self._lock:
            report = await online_evaluate(
                self.store, self.config, metrics=self.metrics
            )
            self.last_report = report
            if self.on_report is not None:
                self.on_report(report)
            return report

    async def run_forever(
        self,
        interval_seconds: float,
        *,
        stop: asyncio.Event | None = None,
        max_batches: int | None = None,
    ) -> None:
        batches = 0
        while stop is None or not stop.is_set():
            if max_batches is not None and batches >= max_batches:
                return
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 — a scoring batch must not kill the loop
                logger.exception("online eval batch failed")
            batches += 1
            if (max_batches is not None and batches >= max_batches) or (
                stop is not None and stop.is_set()
            ):
                return
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise


def create_online_eval_router(
    evaluator: OnlineEvaluator,
    *,
    prefix: str = "/api/evals",
) -> Any:
    """A FastAPI router that runs `evaluator` on demand (needs the `web` extra).

    - `POST {prefix}/run` — score one batch now, return its report;
    - `GET {prefix}/report` — the last batch's report (404 before any run);
    - `GET {prefix}/summary` — just the aggregate scores.

    Imported lazily so the core (`reactifact.eval`, a tracing store) never
    needs FastAPI; mount it next to `create_trace_router` to review the
    results as tags.
    """
    from fastapi import APIRouter, HTTPException

    router = APIRouter(prefix=prefix, tags=["evals"])

    @router.post("/run")
    async def _run() -> dict[str, Any]:
        report = await evaluator.run_once()
        return report.to_dict()

    @router.get("/report")
    async def _report() -> dict[str, Any]:
        if evaluator.last_report is None:
            raise HTTPException(status_code=404, detail="no eval batch has run yet")
        return evaluator.last_report.to_dict()

    @router.get("/summary")
    async def _summary() -> dict[str, Any]:
        if evaluator.last_report is None:
            raise HTTPException(status_code=404, detail="no eval batch has run yet")
        return {
            "sampled": evaluator.last_report.sampled,
            "evaluated": evaluator.last_report.evaluated,
            "failed": evaluator.last_report.failed,
            "overall": round(evaluator.last_report.overall(), 4),
            "aggregate": evaluator.last_report.aggregate(),
        }

    return router


__all__ = [
    "OnlineEvalConfig",
    "OnlineEvaluator",
    "OnlineReport",
    "RunFn",
    "TraceSource",
    "context_source",
    "create_online_eval_router",
    "no_errors",
    "online_evaluate",
    "output_present",
    "trace_source",
]
