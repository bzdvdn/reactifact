"""reactifact.verify — inline verification: `reactifact.eval`'s
ground-truth-free metrics, applied live inside a running `Runtime` instead of
after the fact.

`reactifact.eval`'s `core_metrics` are pure, LLM-free scorers over a `Context`;
batch use (`run_suite`) scores a *finished* `Context` against ground truth after
the pipeline has already completed. `Verify` reuses the same scorers on a
*live* `Context`, right when an agent produces an `Answer` — a live request has
no ground truth, so only the ground-truth-free scorers (`core_metrics` by
default) make sense here; the ground-truth ones (`answer_coverage`,
`calculation_correctness`, ...) stay eval/CI-only.

On failure, `Verify` escalates rather than silently passing or blocking: by
default (`on_fail="ask"`) into the same HITL approve flow the destructive-tool
gate uses (`PendingQuestion(kind="approve")`, `reactifact.tools.use`);
`on_fail="retry"` creates a `VerificationFailed` marker instead, for an agent
that regenerates to consume.

The pass/fail threshold is a framework-wide default
(`RuntimeResources.verification_threshold`) any `Verify` instance can still
override for itself — set once, applies to every `Verify` in a `Runtime`:

    ctx = Context(resources=RuntimeResources(verification_threshold=0.8))
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from .agent import Emit, Step
from .artifacts import Artifact
from .context import Context
from .eval import MetricFn, core_metrics
from .interaction import Human
from .interrupt import PendingQuestion

#: Used when neither the agent nor `RuntimeResources.verification_threshold` sets one.
DEFAULT_THRESHOLD = 0.7


class VerificationResult(BaseModel):
    """Score of one verified `Answer` (§56 metrics, scored live)."""

    answer_id: str
    answer_version: int = 0
    overall: float = 0.0
    threshold: float = DEFAULT_THRESHOLD
    passed: bool = False
    metrics: dict[str, float] = Field(default_factory=dict)
    skipped: list[str] = Field(default_factory=list)
    required_failed: list[str] = Field(default_factory=list)


class VerificationFailed(BaseModel):
    """Marker for `on_fail="retry"`: an agent that regenerates consumes this."""

    answer_id: str
    answer_version: int = 0
    overall: float = 0.0
    threshold: float = DEFAULT_THRESHOLD
    required_failed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class Verify(Step):
    """Scores a live `Answer`-shaped artifact with `eval.py` metrics.

    `threshold`: explicit per-instance cutoff. `None` (default) resolves at
    produce-time from `context.resources.verification_threshold`, itself
    falling back to `DEFAULT_THRESHOLD` when that is also unset — so setting
    it once on `RuntimeResources` changes the bar for every `Verify` in the
    `Runtime`, while a specific agent can still opt out with its own value.

    `required_metrics`: names that must score exactly 1.0 regardless of the
    weighted average — e.g. `("provenance_grounded",)` so an ungrounded
    answer never passes just because other metrics compensate.

    `answer_type`: the artifact type this instance verifies. Pass a model class
    (`Verify(Answer)`) to make the step self-scheduling; a string
    (`Verify("Answer")`) keeps the old by-name mode for callers that do not
    import the domain model, and the containing group must supply the
    `Consume(Answer)`. Other artifact types are ignored.

    `Verify` declares all of its outputs (`VerificationResult`,
    `VerificationFailed`, `PendingQuestion`), so the containing group does not
    need dummy `Step(...)` entries just to widen the allowed `Create` types.
    """

    produces = (VerificationResult, VerificationFailed, PendingQuestion)

    def __init__(
        self,
        answer_type: type[BaseModel] | str = "Answer",
        *,
        metrics: Mapping[str, MetricFn] = core_metrics,
        threshold: float | None = None,
        required_metrics: tuple[str, ...] = (),
        on_fail: Literal["ask", "retry"] = "ask",
        question: str = "Answer failed verification ({reason}). Accept it anyway?",
    ):
        if not isinstance(answer_type, (str, type)):
            raise TypeError("answer_type must be a model class or a class name")
        if isinstance(answer_type, type) and not issubclass(answer_type, BaseModel):
            raise TypeError("answer_type must be a pydantic BaseModel subclass")
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        if on_fail not in ("ask", "retry"):
            raise ValueError("on_fail must be 'ask' or 'retry'")
        self.metrics = dict(metrics)
        unknown = set(required_metrics) - set(self.metrics)
        if unknown:
            raise ValueError(f"unknown required metrics: {sorted(unknown)}")
        self.threshold = threshold
        self.required_metrics = tuple(required_metrics)
        self.on_fail = on_fail
        self.answer_type = answer_type
        self.question = question
        consumes = (answer_type,) if isinstance(answer_type, type) else None
        super().__init__(consumes=consumes)

    def _matches(self, data: BaseModel) -> bool:
        if isinstance(self.answer_type, str):
            return type(data).__name__ == self.answer_type
        return isinstance(data, self.answer_type)

    def _resolve_threshold(self, context: Context) -> float:
        if self.threshold is not None:
            return self.threshold
        configured = context.resources.verification_threshold
        if configured is None:
            return DEFAULT_THRESHOLD
        if not 0.0 <= configured <= 1.0:
            raise ValueError(
                "RuntimeResources.verification_threshold must be between 0 and 1"
            )
        return configured

    async def run(
        self,
        context: Context,
        human: Human,
        trigger: Artifact[Any] | None,
    ) -> Emit | list[Emit] | None:
        answer = trigger
        if answer is None or not self._matches(answer.data):
            return None

        scored: dict[str, float] = {}
        skipped: list[str] = []
        for name, metric_fn in self.metrics.items():
            score = metric_fn(context, None)
            if score is None:
                skipped.append(name)
                continue
            scored[name] = round(max(0.0, min(1.0, score)), 4)

        overall = sum(scored.values()) / len(scored) if scored else 0.0
        required_failed = [
            name for name in self.required_metrics if scored.get(name, 0.0) < 1.0
        ]
        threshold = self._resolve_threshold(context)
        passed = overall >= threshold and not required_failed
        answer_version = answer.version
        result = Emit(
            VerificationResult(
                answer_id=answer.id,
                answer_version=answer_version,
                overall=round(overall, 4),
                threshold=threshold,
                passed=passed,
                metrics=scored,
                skipped=skipped,
                required_failed=required_failed,
            ),
            id=f"verification:{answer.id}:{answer_version}",
            links=(("verifies", answer),),
        )
        if passed:
            return result

        if self.on_fail == "retry":
            return [
                result,
                Emit(
                    VerificationFailed(
                        answer_id=answer.id,
                        answer_version=answer_version,
                        overall=round(overall, 4),
                        threshold=threshold,
                        required_failed=required_failed,
                        skipped=skipped,
                    ),
                    id=f"verification-failed:{answer.id}:{answer_version}",
                    links=(("verifies", answer),),
                ),
            ]

        reason = f"score={overall:.2f}, threshold={threshold:.2f}"
        if required_failed:
            reason += f"; required failed: {', '.join(required_failed)}"
        if skipped:
            reason += f"; skipped: {', '.join(skipped)}"
        human.ask(
            self.question.format(reason=reason),
            kind="approve",
            id=f"verification-approval:{answer.id}:{answer_version}",
            notes={
                "answer_id": answer.id,
                "overall": overall,
                "threshold": threshold,
                "required_failed": required_failed,
                "skipped": skipped,
            },
        )
        return result


__all__ = [
    "DEFAULT_THRESHOLD",
    "VerificationFailed",
    "VerificationResult",
    "Verify",
]
