"""reactifact.eval — dataset evaluation and multi-level scoring (§56).

Two layers, one report:

- **Deterministic metrics** (`scoring.py`) score a finished `Context` — not
  just `answer == expected` but evidence quality, claim verification,
  provenance grounding, calculation correctness, answer/source coverage. All
  LLM-free, matching artifact *classes by name* so the framework needs no
  domain imports.

      from reactifact.eval import EvalCase, core_metrics, run_suite
      report = run_suite(cases, metrics={**core_metrics, "calc": calculation_correctness()})
      print(report.render())

- **The experiment layer** (`dataset.py`, `evaluators.py`, `runner.py`,
  `judge.py`) runs a `Dataset` of `Example`s through a `target` and scores the
  results — deterministic `Evaluator`s, trajectory matching, and optional
  LLM-as-judge:

      from reactifact.eval import Dataset, evaluate, judge_correctness, summary_pass_rate
      report = evaluate(
          Dataset.from_file("evals/qa.json"),
          target=lambda inputs: my_pipeline(**inputs),
          evaluators={"answer": from_metric("answer", answer_present),
                      "correct": judge_correctness(llm)},
          summary=[summary_pass_rate(0.8)],
      )
      report.assert_passed({"correctness": 0.7})   # CI gate

A `target` may return a `Context`, a `ScenarioResult` (from
`reactifact.testing`), an outputs mapping, or a `RunResult`.
"""

from __future__ import annotations

from .dataset import Dataset, Example
from .evaluators import (
    EvalInput,
    Evaluator,
    EvaluatorResult,
    Feedback,
    FeedbackLike,
    StepExtractor,
    TrajectoryMode,
    from_metric,
    trajectory_match,
)
from .judge import (
    CORRECTNESS_INSTRUCTIONS,
    FAITHFULNESS_INSTRUCTIONS,
    JUDGE_SYSTEM,
    RELEVANCE_INSTRUCTIONS,
    JudgeParser,
    judge_correctness,
    judge_faithfulness,
    judge_relevance,
    llm_judge,
    parse_judgement,
)
from .runner import (
    Extractor,
    RunResult,
    SummaryEvaluator,
    Target,
    aevaluate,
    assert_eval,
    default_outputs,
    evaluate,
    summary_mean,
    summary_pass_rate,
)
from .scoring import (
    EvalCase,
    EvalFailure,
    EvalReport,
    EvalResult,
    Metric,
    MetricFn,
    answer_coverage,
    answer_present,
    calculation_correctness,
    claim_verification,
    confidence_calibration,
    core_metrics,
    evidence_quality,
    provenance_grounded,
    run_case,
    run_suite,
    source_coverage,
)

__all__ = [
    "CORRECTNESS_INSTRUCTIONS",
    "Dataset",
    "EvalCase",
    "EvalFailure",
    "EvalInput",
    "EvalReport",
    "EvalResult",
    "Evaluator",
    "EvaluatorResult",
    "Example",
    "Extractor",
    "FAITHFULNESS_INSTRUCTIONS",
    "Feedback",
    "FeedbackLike",
    "JUDGE_SYSTEM",
    "JudgeParser",
    "Metric",
    "MetricFn",
    "RELEVANCE_INSTRUCTIONS",
    "RunResult",
    "StepExtractor",
    "SummaryEvaluator",
    "Target",
    "TrajectoryMode",
    "aevaluate",
    "answer_coverage",
    "answer_present",
    "assert_eval",
    "calculation_correctness",
    "claim_verification",
    "confidence_calibration",
    "core_metrics",
    "default_outputs",
    "evaluate",
    "evidence_quality",
    "from_metric",
    "judge_correctness",
    "judge_faithfulness",
    "judge_relevance",
    "llm_judge",
    "parse_judgement",
    "provenance_grounded",
    "run_case",
    "run_suite",
    "source_coverage",
    "summary_mean",
    "summary_pass_rate",
    "trajectory_match",
]
