---
name: reactifact-eval
description: Evaluate reactifact pipelines and other LLM apps over a dataset with deterministic multi-level metrics, typed trajectory matching, and LLM-as-judge. Use whenever the user wants to measure, benchmark, regression-test, or gate the quality of an agent pipeline or LLM app — building a dataset, scoring answers and provenance, checking an agent's path, adding an LLM judge, or wiring an eval into CI.
---

# Evaluating pipelines with reactifact.eval

`reactifact.eval` has two layers. **Deterministic metrics** score a finished
`Context` — provenance grounding, evidence quality, claim verification,
calculation correctness — not just `answer == expected`. The **experiment
layer** runs a `Dataset` through a target and scores every example. Both are
provider-agnostic and need no hosted store.

## Dataset evaluation

```python
import asyncio
from pydantic import BaseModel
from reactifact import (
    Consume, Context, FakeLLM, Produce, Runtime, RuntimeResources, create_agent,
)
from reactifact.eval import (
    Dataset, answer_present, evaluate, from_metric, judge_relevance,
    summary_pass_rate,
)


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Answerer(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call):
        call.effects.create(Answer(text=call.trigger.data.text.upper()))


agent = create_agent("answerer", consumes=[Consume(Question)], produces=[Answerer()])


def target(inputs):
    # A target may return a Context, a ScenarioResult, an outputs dict, or RunResult.
    ctx = Context(resources=RuntimeResources())
    ctx.create(Question(text=inputs["text"]))
    asyncio.run(Runtime(ctx, agents=[agent]).arun())
    return ctx


dataset = Dataset.from_list(
    [{"inputs": {"text": "hello"}, "reference_outputs": {"answer": "HELLO"}}]
)
report = evaluate(
    dataset,
    target,
    evaluators={
        "present": from_metric("answer_present", answer_present),
        "relevance": judge_relevance(FakeLLM('{"score": true, "comment": "ok"}')),
    },
    summary=[summary_pass_rate(1.0)],
)
report.assert_passed(1.0)          # CI gate; raises EvalFailure with the report
print(report.render())
```

`Dataset`/`Example` hold `inputs` (fed to the target), optional
`reference_outputs` (used only by evaluators) and `metadata`; `Dataset.version`
is a content hash, and `Dataset.from_file("evals/qa.json")` loads `.json` or
`.jsonl`. Sync and async targets both work (a sync target runs in a worker
thread); `max_concurrency=N` parallelizes I/O-bound targets.

## Deterministic metrics

An `Evaluator` is any callable `EvalInput -> Feedback | bool | float |
{key: score} | list | None`; `None` **skips** (no ground truth — never a silent
zero). `EvalInput` carries the example, the extracted `outputs`, and — for
reactifact runs — the final `context` and `trace`. `from_metric` wraps a scoring
metric; the built-ins match artifact classes **by name**:

| Metric | Answers |
| --- | --- |
| `answer_present` | did a run produce an `Answer`? |
| `provenance_grounded` | is every answer backed by an existing `supported_by` edge? |
| `evidence_quality(threshold=…)` | share of `Evidence` scoring at or above the bar |
| `claim_verification(valid=("verified",))` | share of `Claim`s that passed |
| `answer_coverage()` / `calculation_correctness(values=…)` / `source_coverage()` | ground-truth comparisons |
| `confidence_calibration()` | Brier score of `Claim.confidence` |

## Trajectory matching

`trajectory_match` compares the **path** a run took against
`reference_outputs["trajectory"]` — the typed-graph analogue of message
matching. `steps` is `"agents"` (default), `"events"`, `"reads"`/`"writes"`
(`"create:Answer"`, …) or a custom `EvalInput -> list[str]`:

| Mode | Meaning |
| --- | --- |
| `strict` | same steps, same order |
| `unordered` | same multiset of steps |
| `subset` | actual ⊆ expected (no unexpected steps) |
| `superset` | expected ⊆ actual (at least the required steps) |

```python
# not-run: illustrative
evaluate(
    dataset,
    target,
    {"path": trajectory_match("superset", steps="agents")},
)
```

## LLM-as-judge

`llm_judge(llm, instructions=…, key=…)` turns any `LLMProvider` into an
evaluator; `judge_correctness` (reference-based), `judge_relevance` and
`judge_faithfulness` (reference-free) are ready-made. The judge is asked for
strict JSON and parsing is tolerant; `continuous=True` gives a 0..1 float,
`choices=[…]` snaps to a fixed scale. Because a judge is just another LLM call,
it composes with `CachingLLM` (dedupe), budget and metrics, and is faked in
tests with `FakeLLM`.

## Gating and reporting

`EvalReport.aggregate()` is the mean per metric; `passed(...)` /
`assert_passed(...)` / `assert_eval(report, …)` is the gate — a threshold for a
key no case measured is **not** met. Summary evaluators
(`summary_pass_rate(threshold)`, `summary_mean(key)`) aggregate across the
suite. `report.render()` prints a readable report; `report.to_dict()` is a
CI/log artifact.

## Online evaluation

To score **live** runs rather than a dataset, sample them from a `TraceStore`
and reuse the same evaluators — results land as tags on the run plus metrics:

```python
# not-run: illustrative — see references/online.md
from reactifact.eval import OnlineEvalConfig, OnlineEvaluator, output_present

OnlineEvaluator(
    store,
    OnlineEvalConfig(evaluators={"present": output_present()}, sample_rate=0.1),
).run_forever(interval_seconds=300)
```

A trace is truncated, so `trace_source()` (default) scores what the `RunTrace`
carries and skips the rest; `context_source(run_fn)` rebuilds the full `Context`
for exact metrics/judges. Details in `references/online.md`.

## Where to look

- `references/online.md` — scoring live runs from a `TraceStore` (sampling,
  trace vs rehydrated `Context`, tags, metrics, CLI/router).
- `docs/en/eval.md` for the full harness.
- Structure/behaviour tests (not quality) belong to the `reactifact-testing`
  skill.
