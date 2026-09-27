"""Evaluators — score one example's outputs and/or run state (§56).

An `Evaluator` is any callable that receives an `EvalInput` (the example, the
outputs the target produced for it, and — when the target is a reactifact run —
the final `Context` and its `RunTrace`) and returns a score as loose as the
tracing convention allows: a `Feedback`, a `bool`/`float`, a `{key: score}`
mapping, a list of those, or `None` to **skip** (no ground truth / not
applicable — never a silent zero).

The deterministic `MetricFn`s from the scoring core are wrapped into evaluators
with `from_metric`. `trajectory_match` compares the *path* a run took (agent
sequence, event sequence, or artifact ops read/written) against a reference —
the typed-graph analogue of message-trajectory matching.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from ..context import Context
from ..tracing.models import RunTrace
from .dataset import Example
from .scoring import MetricFn


@dataclass
class Feedback:
    """One evaluator's verdict: a named score in 0..1, with an optional note."""

    key: str
    score: float
    comment: str = ""

    def __post_init__(self) -> None:
        self.score = max(0.0, min(1.0, float(self.score)))

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "score": self.score, "comment": self.comment}


@dataclass
class EvalInput:
    """Everything an evaluator may read for one example."""

    example: Example
    outputs: Mapping[str, Any] = field(default_factory=dict)
    context: Context | None = None
    trace: RunTrace | None = None


#: A single per-example verdict, or a list of them, or `None` to skip.
FeedbackLike: TypeAlias = Feedback | bool | int | float | Mapping[str, float] | None
EvaluatorResult: TypeAlias = FeedbackLike | list[FeedbackLike]
Evaluator: TypeAlias = Callable[
    [EvalInput], EvaluatorResult | Awaitable[EvaluatorResult]
]


def from_metric(
    name: str, metric_fn: MetricFn, *, needs_context: bool = True
) -> Evaluator:
    """Wraps a scoring-core `MetricFn` (context, ground truth) -> 0..1.

    Returns `None` (skipped) when the metric has no ground truth or the run
    produced no `Context` to score.
    """

    def evaluate(eval_input: EvalInput) -> Feedback | None:
        if needs_context and eval_input.context is None:
            return None
        context = eval_input.context
        assert context is not None  # narrowed by needs_context
        score = metric_fn(context, eval_input.example.reference_outputs)
        if score is None:
            return None
        return Feedback(key=name, score=float(score))

    evaluate.__name__ = name
    return evaluate


# --------------------------------------------------------------------------- #
# Trajectory match — compare the path a run took against a reference
# --------------------------------------------------------------------------- #

TrajectoryMode: TypeAlias = str  # "strict" | "unordered" | "subset" | "superset"

_TRAJECTORY_MODES = ("strict", "unordered", "subset", "superset")


def _agent_steps(eval_input: EvalInput) -> list[str]:
    trace = eval_input.trace
    return [span.agent for span in trace.spans] if trace is not None else []


def _event_steps(eval_input: EvalInput) -> list[str]:
    trace = eval_input.trace
    return [span.event_type for span in trace.spans] if trace is not None else []


def _op_steps(refs: list[Any], default_op: str) -> list[str]:
    return [f"{ref.op_type or default_op}:{ref.data_type}" for ref in refs]


def _read_steps(eval_input: EvalInput) -> list[str]:
    trace = eval_input.trace
    if trace is None:
        return []
    return [step for span in trace.spans for step in _op_steps(span.reads, "read")]


def _write_steps(eval_input: EvalInput) -> list[str]:
    trace = eval_input.trace
    if trace is None:
        return []
    return [step for span in trace.spans for step in _op_steps(span.writes, "write")]


StepExtractor: TypeAlias = Callable[[EvalInput], list[str]]

_STEP_EXTRACTORS: dict[str, StepExtractor] = {
    "agents": _agent_steps,
    "events": _event_steps,
    "reads": _read_steps,
    "writes": _write_steps,
}


def _resolve_steps(steps: StepExtractor | str) -> StepExtractor:
    if callable(steps):
        return steps
    try:
        return _STEP_EXTRACTORS[steps]
    except KeyError:
        known = ", ".join(sorted(_STEP_EXTRACTORS))
        raise ValueError(
            f"unknown trajectory steps {steps!r} (known: {known})"
        ) from None


def _match(
    actual: list[str], expected: list[str], mode: TrajectoryMode
) -> tuple[bool, str]:
    if mode == "strict":
        ok = actual == expected
    elif mode == "unordered":
        ok = Counter(actual) == Counter(expected)
    elif mode == "subset":
        ok = Counter(actual) <= Counter(expected)
    elif mode == "superset":
        ok = Counter(actual) >= Counter(expected)
    else:
        raise ValueError(
            f"unknown trajectory mode {mode!r} (known: {', '.join(_TRAJECTORY_MODES)})"
        )
    detail = "" if ok else f"actual={actual} expected={expected}"
    return ok, detail


def trajectory_match(
    mode: TrajectoryMode = "strict",
    *,
    steps: StepExtractor | str = "agents",
    reference_key: str = "trajectory",
) -> Evaluator:
    """An `Evaluator` comparing a run's step sequence to a reference trajectory.

    `steps` selects what a "step" is, either a built-in name — `"agents"` (the
    agent path), `"events"` (each span's `event_type`), `"reads"`/`"writes"`
    (`"create:Answer"`, `"update:Evidence"`, …) — or a custom
    `EvalInput -> list[str]`. The expected steps come from
    `example.reference_outputs[reference_key]`.

    Modes mirror agent-trajectory matching: `"strict"` (same steps, same
    order), `"unordered"` (same multiset), `"subset"` (actual ⊆ expected — no
    unexpected steps), `"superset"` (expected ⊆ actual — at least the required
    steps). Skipped when the reference trajectory is absent.
    """
    extractor = _resolve_steps(steps)
    key = f"trajectory_{mode}_match"

    def evaluate(eval_input: EvalInput) -> Feedback | None:
        reference = eval_input.example.reference_outputs
        if reference is None or reference.get(reference_key) is None:
            return None
        expected = [str(step) for step in reference[reference_key]]
        actual = extractor(eval_input)
        ok, detail = _match(actual, expected, mode)
        return Feedback(key=key, score=1.0 if ok else 0.0, comment=detail)

    evaluate.__name__ = key
    return evaluate


__all__ = [
    "EvalInput",
    "Evaluator",
    "EvaluatorResult",
    "Feedback",
    "FeedbackLike",
    "StepExtractor",
    "TrajectoryMode",
    "from_metric",
    "trajectory_match",
]
