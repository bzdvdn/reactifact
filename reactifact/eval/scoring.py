"""Scoring core of `reactifact.eval` (§56) — the deterministic, LLM-free metrics.

Because state is structured, evaluation is not only `answer == expected`: it
separates *evidence quality*, *claim verification*, *provenance grounding*,
*calculation correctness*, *answer coverage* and *source coverage*. Each metric
is a pure function over the final `Context` (+ optional ground truth); the
harness runs a case, collects its metrics, and renders a weighted report.

    cases = [EvalCase("calc-question", run=_run_knowledge_calc)]
    report = run_suite(cases, metrics={**core_metrics, "calc": calculation_correctness()})
    print(report.render())

All metrics are deterministic and LLM-free. They match artifact *classes by
name* (`Answer`, `Evidence`, …) so the harness needs no domain imports — the
domain stays out of the framework. The dataset/experiment layer
(`reactifact.eval.evaluate`, `Dataset`, trajectory + LLM-judge evaluators)
builds on this core; see the package docstring.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..context import Context


class EvalFailure(AssertionError):
    """Raised by `EvalReport.assert_passed`/`assert_eval` when a gate is unmet.

    Subclasses `AssertionError`, so a pytest test that calls
    `report.assert_passed()` fails the ordinary way (and, via `reactifact.testing`,
    with the rendered report inlined in the message).
    """


@dataclass(frozen=True)
class Metric:
    """One measured quantity with a 0..1 score and a reporting weight (§56)."""

    name: str
    score: float = 0.0
    weight: float = 1.0
    note: str = ""


MetricFn = Callable[[Context, Mapping[str, Any] | None], float | None]


@dataclass
class EvalCase:
    """A runnable evaluation case: executes the pipeline and returns its Context."""

    name: str
    run: Callable[[], Context]
    expected: Mapping[str, Any] | None = None


@dataclass
class EvalResult:
    """Scores of one case; `overall` is the weighted mean of its metrics."""

    case: str
    metrics: list[Metric] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def overall(self) -> float:
        metrics = self.metrics or ()
        weights = [m.weight for m in metrics]
        total_weight = sum(weights) or 1.0
        return sum(m.score * m.weight for m in metrics) / total_weight


@dataclass
class EvalReport:
    """The whole suite: one `EvalResult` per case, plus optional summary metrics.

    `summary` holds dataset-level metrics (pass rate, mean of a key, …)
    produced by summary evaluators (`reactifact.eval.summary_*`), which by
    definition cannot be computed per example.
    """

    results: list[EvalResult] = field(default_factory=list)
    summary: list[Metric] = field(default_factory=list)

    def overall(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.overall() for r in self.results) / len(self.results)

    def by_key(self, key: str) -> list[float]:
        """Every score recorded under metric `key`, across all cases."""
        return [m.score for r in self.results for m in r.metrics if m.name == key]

    def aggregate(self) -> dict[str, float]:
        """Mean score per metric key across the suite (missing keys omitted)."""
        keys = sorted({m.name for r in self.results for m in r.metrics})
        return {
            key: round(sum(vals) / len(vals), 4)
            for key in keys
            if (vals := self.by_key(key))
        }

    def passed(
        self,
        thresholds: float | Mapping[str, float] | None = None,
        *,
        overall: float | None = None,
    ) -> bool:
        """Whether the suite clears the given bars (the CI/regression gate).

        `thresholds` is either a single float applied to `overall()`, or a
        mapping of metric key -> minimum *aggregate* (a threshold for a key no
        case measured is **not** met — an unmeasured metric must not silently
        pass). `overall=` adds an explicit bar on the suite mean.
        """
        if overall is not None and self.overall() < overall:
            return False
        if thresholds is None:
            return True
        if isinstance(thresholds, (int, float)):
            return self.overall() >= float(thresholds)
        aggregate = self.aggregate()
        return all(
            aggregate.get(key, float("-inf")) >= bar for key, bar in thresholds.items()
        )

    def assert_passed(
        self,
        thresholds: float | Mapping[str, float] | None = None,
        *,
        overall: float | None = None,
    ) -> EvalReport:
        """`passed(...)` or raise `EvalFailure` with the rendered report."""
        if not self.passed(thresholds, overall=overall):
            raise EvalFailure("eval thresholds not met\n\n" + self.render())
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": round(self.overall(), 4),
            "aggregate": self.aggregate(),
            "summary": [
                {
                    "name": m.name,
                    "score": round(m.score, 4),
                    "weight": m.weight,
                    "note": m.note,
                }
                for m in self.summary
            ],
            "cases": [
                {
                    "case": r.case,
                    "overall": round(r.overall(), 4),
                    "metrics": [
                        {
                            "name": m.name,
                            "score": round(m.score, 4),
                            "weight": m.weight,
                            "note": m.note,
                        }
                        for m in r.metrics
                    ],
                    "skipped": r.skipped,
                }
                for r in self.results
            ],
        }

    def render(self) -> str:
        lines = ["eval · multi-level report (§56)", ""]
        for r in self.results:
            lines.append(f"[{r.case}] overall {r.overall():.3f}")
            for m in r.metrics:
                entry = f"    {m.name:<28} {m.score:6.3f}"
                if m.note:
                    entry += f"  {m.note}"
                lines.append(entry)
            for name in r.skipped:
                lines.append(f"    {name:<28}      — skipped (no ground truth)")
        lines.append(f"\nsuite overall: {self.overall():.3f}")
        if self.summary:
            lines.append("summary:")
            for m in self.summary:
                entry = f"    {m.name:<28} {m.score:6.3f}"
                if m.note:
                    entry += f"  {m.note}"
                lines.append(entry)
        return "\n".join(lines)


def run_case(
    case: EvalCase,
    metrics: Mapping[str, MetricFn],
) -> EvalResult:
    """Executes the case and scores it against every metric."""
    context = case.run()
    result = EvalResult(case=case.name)
    for name, metric_fn in metrics.items():
        score = metric_fn(context, case.expected)
        if score is None:
            result.skipped.append(name)
            continue
        result.metrics.append(
            Metric(name=name, score=round(max(0.0, min(1.0, score)), 4))
        )
    return result


def run_suite(
    cases: list[EvalCase],
    metrics: Mapping[str, MetricFn],
) -> EvalReport:
    return EvalReport(results=[run_case(case, metrics) for case in cases])


# --------------------------------------------------------------------------- #
# Generic helpers — artifact classes are matched *by name* (no domain imports)
# --------------------------------------------------------------------------- #


def _named(context: Context, *class_names: str) -> list[Any]:
    return [a for a in context.list_artifacts() if type(a.data).__name__ in class_names]


def answer_present(
    context: Context, expected: Mapping[str, Any] | None = None
) -> float:
    """1.0 when at least one `Answer` artifact exists."""
    return 1.0 if _named(context, "Answer") else 0.0


def provenance_grounded(
    context: Context, expected: Mapping[str, Any] | None = None
) -> float:
    """Share of answers backed by at least one existing `supported_by` link.

    Provenance correctness (§34): an answer without a resolvable supporting link
    is ungrounded — the graph is the source of truth, not the text.
    """
    answers = _named(context, "Answer")
    if not answers:
        return 0.0
    grounded = sum(1 for a in answers if context.related(a.id, relation="supported_by"))
    return grounded / len(answers)


def evidence_quality(
    context: Context,
    expected: Mapping[str, Any] | None = None,
    *,
    threshold: float = 0.5,
) -> float:
    """Share of `Evidence` artifacts scoring at or above `threshold` (0 if none)."""
    evidences = _named(context, "Evidence")
    if not evidences:
        return 0.0
    good = sum(
        1 for e in evidences if (getattr(e.data, "score", 0.0) or 0.0) >= threshold
    )
    return good / len(evidences)


def claim_verification(
    context: Context,
    expected: Mapping[str, Any] | None = None,
    *,
    valid: tuple[str, ...] = ("verified",),
) -> float:
    """Share of `Claim` artifacts in a `valid` status (1.0 if no claims)."""
    claims = _named(context, "Claim")
    if not claims:
        return 1.0
    good = sum(1 for c in claims if getattr(c.data, "status", "") in valid)
    return good / len(claims)


def confidence_calibration(
    expected_key: str = "claim_correctness",
) -> Callable[[Context, Mapping[str, Any] | None], float | None]:
    """How well `Claim.confidence` predicts actual correctness (Brier score, §56).

    `expected[expected_key]` maps claim artifact id -> bool (was the claim
    actually correct). Score is `1 - mean((confidence - label) ** 2)` over
    claims with ground truth, so 1.0 is perfectly calibrated. This is
    distinct from `claim_verification`: a claim can have `status="verified"`
    with a poorly-calibrated confidence and still pass that metric.
    """

    def _score(context: Context, expected: Mapping[str, Any] | None) -> float | None:
        if expected is None or expected.get(expected_key) is None:
            return None
        labels: Mapping[str, bool] = expected[expected_key]
        claims = _named(context, "Claim")
        scored = [
            (float(getattr(c.data, "confidence", 0.0)), 1.0 if labels[c.id] else 0.0)
            for c in claims
            if c.id in labels
        ]
        if not scored:
            return None
        brier = sum((conf - label) ** 2 for conf, label in scored) / len(scored)
        return 1.0 - brier

    return _score


def answer_coverage(
    expected_key: str = "answer",
) -> Callable[[Context, Mapping[str, Any] | None], float | None]:
    """Coverage of the expected answer text by the actual answer (0..1)."""

    def _score(context: Context, expected: Mapping[str, Any] | None) -> float | None:
        from ..recipes import keyword_score

        if expected is None or expected.get(expected_key) is None:
            return None
        answers = _named(context, "Answer")
        if not answers:
            return 0.0
        return keyword_score(
            str(getattr(answers[0].data, "text", "")), str(expected[expected_key])
        )

    return _score


def calculation_correctness(
    *,
    values: tuple[float, ...] | None = None,
    expected_key: str = "calculations",
) -> Callable[[Context, Mapping[str, Any] | None], float | None]:
    """Share of `Calculation` artifacts whose value matches the ground truth.

    Expected values come from `values` and/or the case metadata `calculations`.
    """

    def _score(context: Context, expected: Mapping[str, Any] | None) -> float | None:
        allowed = set(values or ())
        if expected is not None and expected.get(expected_key) is not None:
            allowed |= {float(v) for v in expected[expected_key]}
        calcs = _named(context, "Calculation")
        if not calcs:
            return 0.0
        if not allowed:
            return None  # no ground truth yet
        good = sum(1 for c in calcs if float(getattr(c.data, "value", 0.0)) in allowed)
        return good / len(calcs)

    return _score


def source_coverage(
    expected_key: str = "sources",
) -> Callable[[Context, Mapping[str, Any] | None], float | None]:
    """Share of the answer's sources matched by the expected source markers."""

    def _score(context: Context, expected: Mapping[str, Any] | None) -> float | None:
        if expected is None or expected.get(expected_key) is None:
            return None
        markers = [str(m) for m in expected[expected_key]]
        answers = _named(context, "Answer")
        if not answers:
            return 0.0
        sources = [s for s in getattr(answers[0].data, "sources", []) if s]
        if not sources:
            return 0.0
        covered = sum(1 for s in sources if any(m in s for m in markers))
        return covered / len(sources)

    return _score


#: The non-generative core metrics, ready to reuse (§56).
core_metrics: dict[str, MetricFn] = {
    "answer_present": answer_present,
    "provenance_grounded": provenance_grounded,
    "evidence_quality": evidence_quality,
    "claim_verification": claim_verification,
}


__all__ = [
    "EvalCase",
    "EvalFailure",
    "EvalReport",
    "EvalResult",
    "Metric",
    "answer_coverage",
    "answer_present",
    "calculation_correctness",
    "claim_verification",
    "confidence_calibration",
    "core_metrics",
    "evidence_quality",
    "provenance_grounded",
    "run_case",
    "run_suite",
    "source_coverage",
]
