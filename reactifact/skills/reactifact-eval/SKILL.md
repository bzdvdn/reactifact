---
name: reactifact-eval
description: Evaluate reactifact pipelines and other LLM apps over a dataset with deterministic multi-level metrics, typed trajectory matching, and LLM-as-judge. Use whenever the user wants to measure, benchmark, regression-test, or gate the quality of an agent pipeline or LLM app — building a dataset, scoring answers and provenance, checking an agent's path, adding an LLM judge, or wiring an eval into CI. Not for structural test assertions (`reactifact-testing`) or live tracing (`reactifact-observability`).
---

# Evaluating pipelines with reactifact.eval

`reactifact.eval` has two offline layers. **Deterministic metrics** score a
finished `Context` — provenance grounding, evidence quality, claim verification,
calculation correctness — not just `answer == expected`. The **experiment
layer** runs a `Dataset` through a target and scores every example. Both are
provider-agnostic and need no hosted store. For live scoring, `Verify` applies
the same ground-truth-free metrics inside a run.

## Dataset evaluation

```python
import asyncio
from pydantic import BaseModel
from reactifact import (
    Agent, Context, FakeLLM, Runtime, RuntimeResources, Step,
)
from reactifact.eval import (
    Dataset, answer_present, evaluate, from_metric, judge_relevance,
    summary_pass_rate,
)


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Answerer(Step):
    reads = (Question,)
    produces = (Answer,)

    async def run(self, question: Question) -> Answer:
        return Answer(text=question.text.upper())


AnswerAgent = Agent("answer_agent", steps=[Answerer()])
agent = AnswerAgent


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

## Retrieval metrics

Retrieval is scored before an answer exists. `evaluate_retrieval` takes ranked
ids (via a `key` callable), ground truth, and `k`; it computes recall,
precision, MRR, nDCG and hit rate. A case without relevant ids is **skipped**,
never a silent zero.

```python
import asyncio
from reactifact.eval import RetrievalCase, evaluate_retrieval

async def retrieve(query, limit):
    return {
        "refund": ["refund.md", "billing.md"],
        "install": ["install.md", "refund.md"],
    }[query][:limit]

report = asyncio.run(
    evaluate_retrieval(
        [
            RetrievalCase("refund", ["refund.md"]),
            RetrievalCase("install", {"install.md": 2.0}),
        ],
        retrieve,
        k=2,
        key=str,
    )
)
assert report.aggregate()["recall"] == 1.0
report.assert_passed({"mrr": 0.7})
```

`relevant` is a list (binary) or a dict `{id: gain}` for graded nDCG. For a
`reactifact.knowledge` pipeline use the ready-made adapter:

```python
# not-run
from reactifact.eval import RetrievalCase
from reactifact.knowledge import cases_from_dataset, chunk_key, evaluate_pipeline

report = await evaluate_pipeline(
    pipeline,
    [RetrievalCase("refund policy", ["refund.md"])],
    k=5,
    hybrid=True,          # forwarded to pipeline.query
)
```

`document_key` (default, by `documentId`/`source_uri`) and `chunk_key` choose
the relevance unit; `cases_from_dataset(dataset)` reads `inputs["query"]` and
`reference_outputs["relevant"]`. An offline baseline can use `HashEmbedder`,
so CI needs no API key.

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

## Inline verification (`reactifact.verify.Verify`)

`Verify` is the live counterpart of batch eval: it scores the `Answer` in the
generation that produced it, emits a stable `VerificationResult`, and on failure
either asks a human (`PendingQuestion(kind="approve")`) or emits
`VerificationFailed` for a regenerating step. Pass the model class to make it
self-scheduling:

```python
# not-run: illustrative
from reactifact import Agent
from reactifact.verify import Verify

Answerer = Agent("answerer", steps=[BuildAnswer(), Verify(Answer)])
```

The containing group needs no dummy `Step(...)` entries: `Verify` declares its
own `VerificationResult`/`VerificationFailed`/`PendingQuestion` outputs. See
`docs/en/testing.md` for thresholds and `on_fail`.

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
- `docs/en/eval.md` for the full harness; `docs/en/knowledge.md` for the
  knowledge pipeline and its retrieval adapter.
- Structure/behaviour tests (not quality) belong to the `reactifact-testing`
  skill.
