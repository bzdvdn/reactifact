"""Running a dataset through a target and scoring it — the experiment layer (§56).

`evaluate(dataset, target, evaluators)` is the offline loop: for each example,
call `target(inputs)`, extract the outputs, and run every evaluator, collecting
per-example `Feedback` into the same `EvalReport` the scoring core renders.

`target` is any callable `inputs -> result`, where `result` may be
- a `Context` (a reactifact run — outputs are read off it via `extract`),
- a `ScenarioResult`/anything exposing `.context`/`.trace` (so a
  `reactifact.testing` scenario drops in directly), or
- a plain `{...}` outputs mapping (any non-reactifact app).

`summary` evaluators aggregate across the whole suite (pass rate, mean of a
key) into `EvalReport.summary`; `report.assert_passed(...)` is the CI gate.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from ..context import Context
from ..tracing.models import RunTrace
from .dataset import Dataset, Example
from .evaluators import EvalInput, Evaluator, Feedback
from .scoring import EvalReport, EvalResult, Metric

#: Anything a target may return (a Context, a mapping, a run object, a tuple).
Target: TypeAlias = Callable[[Mapping[str, Any]], Any]
Extractor: TypeAlias = Callable[[Context], Mapping[str, Any]]


@dataclass
class RunResult:
    """Normalized output of one target call: outputs, and run state if any."""

    context: Context | None = None
    outputs: Mapping[str, Any] = field(default_factory=dict)
    trace: RunTrace | None = None


def default_outputs(context: Context) -> Mapping[str, Any]:
    """Reads a target's outputs off its final `Context`.

    Prefers the latest `Answer`-named artifact (`{"answer": <text or dump>}`,
    plus its `sources` when present) — the same by-name convention the scoring
    metrics use. Falls back to every artifact grouped by type name, so a run
    with no `Answer` still yields *some* outputs for the judge to read.
    """
    artifacts = context.list_artifacts()
    answers = [a for a in artifacts if type(a.data).__name__ == "Answer"]
    if answers:
        data = answers[-1].data
        text = getattr(data, "text", None)
        outputs: dict[str, Any] = {
            "answer": text if text is not None else data.model_dump()
        }
        sources = getattr(data, "sources", None)
        if sources:
            outputs["sources"] = list(sources)
        return outputs
    by_type: dict[str, list[Any]] = {}
    for artifact in artifacts:
        by_type.setdefault(type(artifact.data).__name__, []).append(
            artifact.data.model_dump()
        )
    return {"artifacts": by_type}


def _normalize_run(value: Any, extract: Extractor) -> RunResult:
    """Coerces whatever `target` returned into a `RunResult`."""
    if isinstance(value, RunResult):
        return value
    if isinstance(value, Context):
        return RunResult(context=value, outputs=extract(value))
    if isinstance(value, Mapping):
        return RunResult(outputs=dict(value))
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], Context):
        context = value[0]
        trace = value[1]
        return RunResult(
            context=context,
            outputs=extract(context),
            trace=trace if isinstance(trace, RunTrace) else None,
        )
    context_obj = getattr(value, "context", None)
    if isinstance(context_obj, Context):
        trace = getattr(value, "trace", None)
        return RunResult(
            context=context_obj,
            outputs=extract(context_obj),
            trace=trace if isinstance(trace, RunTrace) else None,
        )
    raise TypeError(
        "target must return a Context, an outputs mapping, a run object with a "
        f".context, or a (Context, trace) tuple — got {type(value).__name__}"
    )


def _as_feedbacks(value: Any, fallback_key: str) -> tuple[list[Feedback], bool]:
    """Normalizes an evaluator's loose return into `(feedback, skipped)`."""
    if value is None:
        return [], True
    if isinstance(value, Feedback):
        return [value], False
    if isinstance(value, bool):
        return [Feedback(fallback_key, 1.0 if value else 0.0)], False
    if isinstance(value, (int, float)):
        return [Feedback(fallback_key, float(value))], False
    if isinstance(value, Mapping):
        return [Feedback(str(k), float(v)) for k, v in value.items()], False
    if isinstance(value, (list, tuple)):
        items: list[Feedback] = []
        for item in value:
            if isinstance(item, Feedback):
                items.append(item)
            elif isinstance(item, Mapping) and "key" in item:
                items.append(
                    Feedback(
                        str(item["key"]),
                        float(item.get("score", 0.0)),
                        str(item.get("comment", "")),
                    )
                )
            else:
                raise TypeError(f"unsupported evaluator list item: {item!r}")
        return items, False
    raise TypeError(f"unsupported evaluator result: {value!r}")


def _evaluator_name(evaluator: Any) -> str:
    return getattr(evaluator, "__name__", "evaluator")


async def _run_evaluator(evaluator: Evaluator, eval_input: EvalInput) -> Any:
    value = evaluator(eval_input)
    if inspect.isawaitable(value):
        value = await value
    return value


async def _evaluate_one(
    example: Example,
    *,
    target: Target,
    evaluators: Sequence[tuple[str, Evaluator]],
    extract: Extractor,
) -> tuple[EvalResult, EvalInput]:
    raw = target(example.inputs)
    if inspect.isawaitable(raw):
        raw = await raw
    run = _normalize_run(raw, extract)
    eval_input = EvalInput(
        example=example, outputs=run.outputs, context=run.context, trace=run.trace
    )
    result = EvalResult(case=example.id)
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
    return result, eval_input


SummaryEvaluator: TypeAlias = Callable[[list[EvalResult]], Any]


def _as_dataset(dataset: Dataset | Iterable[Example | Mapping[str, Any]]) -> Dataset:
    if isinstance(dataset, Dataset):
        return dataset
    return Dataset.from_list(dataset)


def _evaluator_list(
    evaluators: Mapping[str, Evaluator] | Sequence[Evaluator],
) -> list[tuple[str, Evaluator]]:
    """Normalizes evaluators to `(name, evaluator)`, using mapping keys as names."""
    if isinstance(evaluators, Mapping):
        return [(str(key), evaluator) for key, evaluator in evaluators.items()]
    return [(_evaluator_name(evaluator), evaluator) for evaluator in evaluators]


async def aevaluate(
    dataset: Dataset | Iterable[Example | Mapping[str, Any]],
    target: Target,
    evaluators: Mapping[str, Evaluator] | Sequence[Evaluator],
    *,
    extract: Extractor = default_outputs,
    summary: Sequence[SummaryEvaluator] = (),
    max_concurrency: int = 0,
) -> EvalReport:
    """Runs `target` over every example and scores it (async).

    `max_concurrency=0` (default) runs examples sequentially in dataset order,
    keeping the report deterministic; a positive value runs at most that many
    examples at once (`asyncio`), for large suites where the target is I/O-bound.
    """
    ds = _as_dataset(dataset)
    evaluators_list = _evaluator_list(evaluators)

    async def one(example: Example) -> tuple[EvalResult, EvalInput]:
        return await _evaluate_one(
            example, target=target, evaluators=evaluators_list, extract=extract
        )

    if max_concurrency and max_concurrency > 0:
        semaphore = asyncio.Semaphore(max_concurrency)

        async def guarded(example: Example) -> tuple[EvalResult, EvalInput]:
            async with semaphore:
                return await one(example)

        outcomes = await asyncio.gather(*(guarded(e) for e in ds.examples))
    else:
        outcomes = [await one(e) for e in ds.examples]

    report = EvalReport(results=[result for result, _ in outcomes])
    for summary_evaluator in summary:
        value = summary_evaluator(report.results)
        if inspect.isawaitable(value):
            value = await value
        feedbacks, _ = _as_feedbacks(value, _evaluator_name(summary_evaluator))
        for feedback in feedbacks:
            report.summary.append(
                Metric(
                    name=feedback.key,
                    score=round(max(0.0, min(1.0, feedback.score)), 4),
                    note=feedback.comment,
                )
            )
    return report


def evaluate(
    dataset: Dataset | Iterable[Example | Mapping[str, Any]],
    target: Target,
    evaluators: Mapping[str, Evaluator] | Sequence[Evaluator],
    *,
    extract: Extractor = default_outputs,
    summary: Sequence[SummaryEvaluator] = (),
    max_concurrency: int = 0,
) -> EvalReport:
    """Synchronous `aevaluate` — mirrors `Runtime.run`/`ScenarioLab.run_sync`."""
    return asyncio.run(
        aevaluate(
            dataset,
            target,
            evaluators,
            extract=extract,
            summary=summary,
            max_concurrency=max_concurrency,
        )
    )


def summary_pass_rate(threshold: float = 1.0) -> SummaryEvaluator:
    """A summary evaluator: share of cases whose weighted `overall` clears `threshold`."""

    def summarise(results: list[EvalResult]) -> Feedback:
        passed = sum(1 for r in results if r.overall() >= threshold)
        total = len(results) or 1
        return Feedback(
            key="pass_rate",
            score=passed / total,
            comment=f"{passed}/{len(results)} cases ≥ {threshold}",
        )

    return summarise


def summary_mean(key: str) -> SummaryEvaluator:
    """A summary evaluator: mean of metric `key` across all cases."""

    def summarise(results: list[EvalResult]) -> Feedback | None:
        scores = [m.score for r in results for m in r.metrics if m.name == key]
        if not scores:
            return None
        return Feedback(key=f"mean_{key}", score=sum(scores) / len(scores))

    return summarise


def assert_eval(
    report: EvalReport,
    thresholds: float | Mapping[str, float] | None = None,
    *,
    overall: float | None = None,
) -> EvalReport:
    """The CI gate: `report.assert_passed(...)` (raises `EvalFailure`)."""
    return report.assert_passed(thresholds, overall=overall)


__all__ = [
    "Extractor",
    "RunResult",
    "SummaryEvaluator",
    "Target",
    "aevaluate",
    "assert_eval",
    "default_outputs",
    "evaluate",
    "summary_mean",
    "summary_pass_rate",
]
