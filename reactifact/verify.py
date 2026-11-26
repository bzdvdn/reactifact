"""reactifact.verify — inline verification: `reactifact.eval`'s
ground-truth-free metrics, applied live inside a running `Runtime` instead of
after the fact.

`reactifact.eval`'s `core_metrics` are pure, LLM-free scorers over a `Context`; batch
use (`run_suite`) scores a *finished* `Context` against ground truth after
the pipeline has already completed. `Verify` reuses the same scorers on a
*live* `Context`, right when an agent produces an `Answer` — a live request
has no ground truth, so only the ground-truth-free scorers (`core_metrics`
by default) make sense here; the ground-truth ones (`answer_coverage`,
`calculation_correctness`, ...) stay eval/CI-only.

On failure, `Verify` escalates rather than silently passing or blocking: by
default (`on_fail="ask"`) into the same HITL approve flow the destructive-tool
gate uses (`PendingQuestion(kind="approve")`, `reactifact.tools.use`);
`on_fail="retry"`
creates a `VerificationFailed` marker instead, for an agent that regenerates
to consume.

The pass/fail threshold is a framework-wide default
(`RuntimeResources.verification_threshold`) any `Verify` instance can still
override for itself — set once, applies to every `Verify` in a `Runtime`:

    ctx = Context(resources=RuntimeResources(verification_threshold=0.8))
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from .artifacts import Artifact
from .context import Context
from .effects import Effects
from .eval import MetricFn, core_metrics
from .interaction import Human
from .step import Step

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


class Verify(Step[VerificationResult]):
    """Scores a live `Answer`-shaped artifact with `eval.py` metrics.

    `threshold`: explicit per-instance cutoff. `None` (default) resolves at
    produce-time from `context.resources.verification_threshold`, itself
    falling back to `DEFAULT_THRESHOLD` when that is also unset — so setting
    it once on `RuntimeResources` changes the bar for every `Verify` in the
    `Runtime`, while a specific agent can still opt out with its own value.

    `required_metrics`: names that must score exactly 1.0 regardless of the
    weighted average — e.g. `("provenance_grounded",)` so an ungrounded
    answer never passes just because other metrics compensate.

    `answer_type`: the artifact class name (by name, no domain import —
    same convention as `eval.py`) this instance verifies; artifacts of any
    other type are ignored.

    On top of `VerificationResult`, this creates a `PendingQuestion`
    (`on_fail="ask"`) or a `VerificationFailed` (`on_fail="retry"`) — the
    containing agent's `produces` must declare those too (a bare
    `Step(PendingQuestion)`/`Step(VerificationFailed)` widens the
    allowed `Create` types without adding real logic — same convention
    `HITLLMAgent` uses for `Observation`/`PendingQuestion`, `reactifact.llm_agent`):

        class Answerer(StepGroup):
            consumes = [Consume(Question)]
            produces = [BuildAnswer(), Verify(), Step(PendingQuestion)]
    """

    artifact_type = VerificationResult

    def __init__(
        self,
        *,
        metrics: Mapping[str, MetricFn] = core_metrics,
        threshold: float | None = None,
        required_metrics: tuple[str, ...] = (),
        on_fail: Literal["ask", "retry"] = "ask",
        answer_type: str = "Answer",
        question: str = "Answer failed verification ({reason}). Accept it anyway?",
    ):
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
        super().__init__()

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
        self, context: Context, effects: Effects, trigger: Artifact[Any] | None
    ) -> None:
        answer = trigger
        if answer is None or type(answer.data).__name__ != self.answer_type:
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

        effects.create(
            VerificationResult(
                answer_id=answer.id,
                answer_version=answer.version,
                overall=round(overall, 4),
                threshold=threshold,
                passed=passed,
                metrics=scored,
                skipped=skipped,
                required_failed=required_failed,
            )
        ).link("verifies", answer)
        if passed:
            return None

        if self.on_fail == "retry":
            effects.create(
                VerificationFailed(
                    answer_id=answer.id,
                    answer_version=answer.version,
                    overall=round(overall, 4),
                    threshold=threshold,
                    required_failed=required_failed,
                    skipped=skipped,
                )
            ).link("verifies", answer)
            return None

        reason = f"score={overall:.2f}, threshold={threshold:.2f}"
        if required_failed:
            reason += f"; required failed: {', '.join(required_failed)}"
        if skipped:
            reason += f"; skipped: {', '.join(skipped)}"
        Human(effects).ask(
            self.question.format(reason=reason),
            kind="approve",
            notes={
                "answer_id": answer.id,
                "overall": overall,
                "threshold": threshold,
                "required_failed": required_failed,
                "skipped": skipped,
            },
        )
        return None


__all__ = [
    "DEFAULT_THRESHOLD",
    "VerificationFailed",
    "VerificationResult",
    "Verify",
]
