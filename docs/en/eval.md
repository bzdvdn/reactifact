# Evaluation harness (§56)

Because state is structured, evaluation is **multi-level** — not just
`answer == expected`:

> Evidence quality · Claim verification · Confidence calibration ·
> Provenance grounding · Calculation correctness · Answer coverage ·
> Source coverage

`reactifact.eval` is the deterministic, LLM-free harness that scores a run's final
**state**. Each metric is a pure function over the resulting `Context` (+
optional ground truth), so truthfulness is measured where it lives — the
artifact graph — not the smoothness of the text.

## Running a suite

```python
from reactifact.eval import EvalCase, calculation_correctness, core_metrics, run_suite

cases = [
    EvalCase(
        name="calc-question",
        run=_run_knowledge_calc,               # executes the pipeline → Context
        expected={"sources": ["costs:", "pricing:", "guide:"]},
    ),
]
report = run_suite(cases, metrics={
    **core_metrics,                          # answer/provenance/evidence/claim
    "calc": calculation_correctness(values=(5480, 3580)),
    "sources": source_coverage(),
})
print(report.render())
```

```
eval · multi-level report (§56)

[calc-question] overall 1.000
    answer_present            1.000
    provenance_grounded       1.000
    evidence_quality          1.000
    claim_verification        1.000
    calc                      1.000
    sources                   1.000

suite overall: 1.000
```

## Metrics

Artifact classes are matched **by name** (`Answer`, `Evidence`, …), so the
harness needs no domain imports — the domain stays out of the framework.

| Metric | What it answers | Built-in |
| --- | --- | --- |
| `answer_present` | did the run produce an answer at all? | plain fn |
| `provenance_grounded` | is every answer backed by an existing `supported_by` (§34)? | plain fn |
| `evidence_quality(threshold=0.5)` | share of evidence scoring at/above the bar | plain fn |
| `claim_verification(valid=("verified",))` | share of claims that passed verification (§35) | plain fn |
| `confidence_calibration()` | Brier score of `Claim.confidence` against actual correctness (§56) | factory (needs `expected.claim_correctness`) |
| `answer_coverage()` | coverage of the expected answer text by the answer | factory (needs `expected.answer`) |
| `calculation_correctness(values=…)` | share of calculations matching ground truth (§67) | factory |
| `source_coverage()` | share of the answer's sources matched by markers | factory (needs `expected.sources`) |

`core_metrics` bundles the four non-generative ones. A metric that lacks ground
truth returns `None` and is reported as **skipped** (`EvalResult.skipped`), never
as a silent zero.

## Dataset evaluation

Beyond scoring one already-built `Context`, `reactifact.eval` runs a **dataset**
through a **target** and scores every example — the offline-evaluation loop,
with no hosted store:

```python
from reactifact.eval import (
    Dataset, evaluate, from_metric, judge_correctness, summary_pass_rate,
)

dataset = Dataset.from_file("evals/qa.json")       # or from_list / .jsonl
report = evaluate(
    dataset,
    target=lambda inputs: my_pipeline(**inputs),   # Context | ScenarioResult | {outputs} | RunResult
    evaluators={
        "present": from_metric("answer_present", answer_present),
        "coverage": from_metric("coverage", answer_coverage()),
        "correctness": judge_correctness(llm),      # P2: LLM-as-judge
    },
    summary=[summary_pass_rate(0.8)],
)
report.assert_passed({"correctness": 0.7})          # CI gate (raises EvalFailure)
```

`Example` carries `inputs` (fed to the target), optional `reference_outputs`
(used only by evaluators) and `metadata`; its `id` is content-derived, so
`Dataset.version` pins a run to a dataset revision. A target may return a
`Context`, a `ScenarioResult` from `reactifact.testing` (read via `.context`/
`.trace`), a plain outputs mapping, or a `RunResult`. `evaluate` runs examples
sequentially in dataset order (`max_concurrency=N` parallelizes I/O-bound
targets).

## Evaluators

An `Evaluator` is any callable `EvalInput -> Feedback | bool | float |
{key: score} | list | None`; `None` **skips** (no ground truth / not applicable
— never a silent zero). `EvalInput` exposes the example, the extracted
`outputs`, and — for reactifact runs — the final `context` and `trace`.
`from_metric` wraps any scoring metric into an evaluator:

```python
evaluators = {
    "grounded": from_metric("provenance_grounded", provenance_grounded),
    "calc": from_metric("calc", calculation_correctness(values=(5480, 3580))),
}
```

`trajectory_match(mode, steps=…)` compares the **path** a run took against
`example.reference_outputs["trajectory"]` — the typed-graph analogue of
message-trajectory matching. `steps` is `"agents"` (the agent path),
`"events"`, `"reads"`/`"writes"` (`"create:Answer"`, …) or a custom
`EvalInput -> list[str]`:

| Mode | Meaning |
| --- | --- |
| `strict` | same steps, same order |
| `unordered` | same multiset of steps |
| `subset` | actual ⊆ expected (no unexpected steps) |
| `superset` | expected ⊆ actual (at least the required steps) |

## LLM-as-judge

For subjective quality there is no deterministic metric; an LLM grades the
output against a rubric. `llm_judge` turns any `LLMProvider` into an
`Evaluator`, so a judge is just another model call — it composes with
`CachingLLM`/budget/metrics and is faked in tests with `FakeLLM`:

```python
from reactifact.eval import judge_correctness, judge_faithfulness, judge_relevance

judge_correctness(llm, continuous=True, choices=[0.0, 0.5, 1.0])   # reference-based
judge_relevance(llm)        # reference-free: does it answer the question?
judge_faithfulness(llm)     # reference-free: grounded in the context?
```

The judge is asked for strict JSON (`{"score": …, "comment": …}`); parsing is
tolerant (scans for the first JSON object, then a number/boolean). `continuous`
yields a 0..1 float; `choices` snaps to a fixed scale; `include_reference`
formats the reference outputs into the prompt (reference-based grading).

## Gate & summary

`EvalReport.aggregate()` is the mean per metric key; `passed(…)` /
`assert_passed(…)` (or module-level `assert_eval`) is the CI gate — a threshold
for a key no case measured is **not** met. Summary evaluators
(`summary_pass_rate(threshold)`, `summary_mean(key)`) aggregate across the whole
suite into `EvalReport.summary`.

## Online evaluation

The same evaluators can score **live** runs instead of a curated dataset:
`reactifact.eval.online` samples finished runs from a `TraceStore`, scores them,
and writes the verdicts back as tags (the reviewer-annotation channel the
dashboard already reads) plus metrics.

```python
from reactifact.eval import (
    OnlineEvalConfig, OnlineEvaluator, judge_relevance, output_present,
)
from reactifact.tracing import TraceStore

store = TraceStore("traces.db")
evaluator = OnlineEvaluator(
    store,
    OnlineEvalConfig(
        evaluators={"present": output_present(), "relevance": judge_relevance(llm)},
        sample_rate=0.1,            # score 10% of matching runs
        tag="eval", failure_tag="eval:failed",
    ),
    metrics=metrics,                # any reactifact.metrics.Metrics
)
await evaluator.run_once()          # one batch
# or, in a FastAPI lifespan task:
await evaluator.run_forever(interval_seconds=300)
```

A trace is a **summary** — artifact data and LLM messages are truncated for
storage — so a source decides what is scoreable:

- `trace_source()` (default): scores the `RunTrace` itself — the run's typed
  path (`trajectory_match`), and `output_present`/`no_errors` over the outputs
  and spans. A metric that needs data the trace no longer carries is **skipped**.
- `context_source(run_fn)`: the app rebuilds the full `Context` for a run id, so
  `core_metrics`, `from_metric(...)` and judges see complete data.
- `OnlineEvalConfig.reference_fn(run)` supplies per-run ground truth (e.g. an
  expected trajectory) when scoring a reference-based metric.

`sample_rate`/`strategy`/`seed` choose which runs (reproducible under a `seed`);
`on_report` observes each batch. FastAPI apps can mount
`create_online_eval_router(evaluator)` — `POST /api/evals/run`,
`GET /api/evals/report`, `GET /api/evals/summary` — and the CLI scores a store
in one command:

```bash
reactifact eval traces.db --sample 1.0
reactifact eval traces.db --evaluators judge --provider openai:gpt-4o-mini
```

## Types

- `Metric` — one measured 0..1 score with a reporting `weight`.
- `EvalCase` — name + a `run() -> Context` + optional `expected` ground truth.
- `EvalResult` (per case) / `EvalReport` (suite) — `overall()` (weighted mean),
  `aggregate()`, `passed()`, `to_dict()`, `render()`.
- `Example` / `Dataset` — versioned evaluation examples + JSON/JSONL loading.
- `EvalInput` / `Feedback` / `Evaluator` — an evaluator's inputs and verdict.
- `RunResult` — a normalized target result (`outputs` + optional context/trace).
- `EvalFailure` — raised by the gate (`AssertionError` subclass).

## Where to look

`tests/test_eval.py` scores the `knowledge` calculation question offline (no
LLM); `tests/test_eval_dataset.py`, `test_eval_trajectory.py` and
`test_eval_judge.py` cover the dataset loop, trajectory matching and the judge
(a `FakeLLM` returning JSON).