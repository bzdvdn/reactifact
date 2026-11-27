"""Experiments/Arena: run variants on one dataset, score, and pick a winner.

An `Experiment` is branch + eval, wired:

    result = await Experiment(
        base=context,
        variants={"gpt": gpt_agents, "claude": claude_agents},
        dataset=Dataset.from_file("evals/qa.json"),
        seed=seed_question,
        evaluators=[exact_answer],
    ).run()

Each variant runs on its own `base.branch()` per example (no cross-variant
mutation), the existing `reactifact.eval` evaluators score it, and the report
ranks quality with cost/latency as tie-breakers. The returned per-variant
contexts are real branches: inspect them, or merge a chosen one yourself.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .agent import Agent
from .budget import Budget, RunStats
from .context import Context
from .eval.dataset import Dataset, Example
from .eval.evaluators import Evaluator
from .eval.runner import Extractor, aevaluate, default_outputs
from .eval.scoring import EvalReport, EvalResult
from .runtime import Runtime
from .tracing import Tracer

Seed = Callable[[Context, Mapping[str, Any]], Any]
OnResult = Callable[[str, EvalResult], None]


@dataclass(frozen=True)
class ExperimentVariant:
    name: str
    agents: Sequence[Agent]
    description: str = ""


@dataclass
class VariantResult:
    name: str
    report: EvalReport
    stats: list[RunStats] = field(default_factory=list)
    contexts: list[Context] = field(default_factory=list)

    @property
    def cost(self) -> float:
        return sum(stat.cost for stat in self.stats)

    @property
    def tokens(self) -> int:
        return sum(stat.total_tokens for stat in self.stats)

    @property
    def avg_latency(self) -> float:
        if not self.stats:
            return 0.0
        return sum(stat.duration for stat in self.stats) / len(self.stats)

    def quality(self, metric: str | None = None) -> float:
        if metric is None:
            return self.report.overall()
        scores = self.report.by_key(metric)
        return sum(scores) / len(scores) if scores else 0.0


@dataclass
class ExperimentReport:
    variants: list[VariantResult]
    winner: str
    dataset_version: str
    metric: str | None = None

    @property
    def winner_result(self) -> VariantResult:
        for variant in self.variants:
            if variant.name == self.winner:
                return variant
        raise KeyError(self.winner)

    def table(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for variant in self.variants:
            rows.append(
                {
                    "variant": variant.name,
                    "quality": round(variant.quality(self.metric), 4),
                    "cost": round(variant.cost, 6),
                    "avg_latency_s": round(variant.avg_latency, 4),
                    "tokens": variant.tokens,
                }
            )
        return rows

    def render(self) -> str:
        lines = [
            "| variant | quality | cost | avg latency (s) | tokens |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for row in self.table():
            marker = " *" if row["variant"] == self.winner else ""
            lines.append(
                f"| {row['variant']}{marker} | {row['quality']:.4f} | "
                f"{row['cost']:.6f} | {row['avg_latency_s']:.4f} | "
                f"{row['tokens']} |"
            )
        lines.append("")
        lines.append(f"winner: **{self.winner}**")
        return "\n".join(lines)


class Experiment:
    """Runs `variants` over a dataset and returns an `ExperimentReport`.

    `seed(context, inputs)` writes the example's inputs into the fresh branch
    (sync or async). `evaluators` are the normal `reactifact.eval` callables;
    `metric=` selects which aggregate decides quality (default: the report's
    weighted `overall`). Cost/latency break ties between equal-quality variants.
    """

    def __init__(
        self,
        *,
        base: Context,
        variants: Mapping[str, Sequence[Agent] | Agent | ExperimentVariant],
        dataset: Dataset | Iterable[Example | Mapping[str, Any]],
        seed: Seed,
        evaluators: Mapping[str, Evaluator] | Sequence[Evaluator],
        extract: Extractor = default_outputs,
        metric: str | None = None,
        budget: Budget | None = None,
        tracer: Tracer | None = None,
        max_concurrency: int = 0,
        max_iterations: int = 100,
        name: str = "experiment",
        on_result: OnResult | None = None,
    ):
        self.base = base
        self.variants: list[ExperimentVariant] = []
        for name, value in variants.items():
            if isinstance(value, ExperimentVariant):
                self.variants.append(value)
            else:
                self.variants.append(
                    ExperimentVariant(
                        name=name,
                        agents=([value] if isinstance(value, Agent) else list(value)),
                    )
                )
        if not self.variants:
            raise ValueError("Experiment needs at least one variant")
        self.dataset = (
            dataset if isinstance(dataset, Dataset) else Dataset.from_list(dataset)
        )
        self.seed = seed
        self.evaluators = evaluators
        self.extract = extract
        self.metric = metric
        self.budget = budget
        self.tracer = tracer
        self.max_concurrency = max_concurrency
        self.max_iterations = max_iterations
        self.name = name
        self.on_result = on_result

    async def _run_variant(self, variant: ExperimentVariant) -> VariantResult:
        stats: list[RunStats] = []
        contexts: list[Context] = []

        async def target(inputs: Mapping[str, Any]) -> Context:
            branch = self.base.branch(
                name=f"{self.name}:{variant.name}:{len(contexts)}"
            )
            branch.replace_events([])
            seeded = self.seed(branch, inputs)
            if inspect.isawaitable(seeded):
                await seeded
            runtime = Runtime(
                branch,
                groups=list(variant.agents),
                budget=self.budget,
                tracer=self.tracer,
            )
            await runtime.arun(max_iterations=self.max_iterations)
            if runtime.last_stats is not None:
                stats.append(runtime.last_stats)
            contexts.append(branch)
            return branch

        def on_result(result: EvalResult) -> None:
            if self.on_result is not None:
                self.on_result(variant.name, result)

        report = await aevaluate(
            self.dataset,
            target,
            self.evaluators,
            extract=self.extract,
            max_concurrency=self.max_concurrency,
            on_result=on_result,
        )
        return VariantResult(
            name=variant.name,
            report=report,
            stats=stats,
            contexts=contexts,
        )

    async def run(self) -> ExperimentReport:
        results = [await self._run_variant(variant) for variant in self.variants]
        qualities = {result.name: result.quality(self.metric) for result in results}
        best = max(qualities.values())
        tied = [
            result for result in results if abs(qualities[result.name] - best) < 1e-9
        ]
        winner = min(
            tied, key=lambda result: (result.cost, result.avg_latency, result.name)
        )
        return ExperimentReport(
            variants=results,
            winner=winner.name,
            dataset_version=self.dataset.version,
            metric=self.metric,
        )


__all__ = [
    "Experiment",
    "ExperimentReport",
    "ExperimentVariant",
    "VariantResult",
]
