# Online evaluation

Score **live** runs instead of a curated dataset. `reactifact.eval.online`
samples finished runs from a `TraceStore`, runs the same `Evaluator`s, and
writes the verdicts back as tags (pass → `eval`, fail → `eval:failed`) — the
reviewer-annotation channel the dashboard already reads — plus metrics.

```python
# not-run: illustrative — `llm`/`metrics` are wired by the app
from reactifact.eval import OnlineEvalConfig, OnlineEvaluator, judge_relevance, output_present
from reactifact.tracing import TraceStore

store = TraceStore("traces.db")
evaluator = OnlineEvaluator(
    store,
    OnlineEvalConfig(
        evaluators={"present": output_present(), "relevance": judge_relevance(llm)},
        sample_rate=0.1,
        tag="eval", failure_tag="eval:failed",
    ),
    metrics=metrics,
)
await evaluator.run_once()                    # one batch
await evaluator.run_forever(interval_seconds=300)   # or a background task
```

## The truncation problem, and the two sources

A trace is a summary: artifact `data` and LLM messages are truncated for
storage (`RuntimeResources.trace_truncate`). So a **source** decides what is
scoreable:

| Source | Scores | Use |
| --- | --- | --- |
| `trace_source()` (default) | the `RunTrace`: its typed path (`trajectory_match`), `output_present`, `no_errors` | cheap, always available |
| `context_source(run_fn)` | a rebuilt `Context`: `core_metrics`, `from_metric(...)`, LLM judges | exact, needs a persisted run |

A metric that needs data the trace no longer carries returns `None` and is
reported as **skipped** — never a false zero. That is why a `from_metric`
evaluator (which needs a `Context`) is skipped under `trace_source` and works
under `context_source`.

## Config

`OnlineEvalConfig`:

| Field | Meaning |
| --- | --- |
| `evaluators` | mapping name → `Evaluator` (same as offline) |
| `sample_rate`, `strategy` (`random`/`head`), `seed` | which runs, reproducibly |
| `limit`, `session_id`, `outcome`, `tags` | the store query |
| `source` | `trace_source()` or `context_source(run_fn)` |
| `reference_fn(run)` | per-run ground truth (e.g. expected trajectory) |
| `annotate`, `tag`, `failure_tag`, `passed_threshold` | how results are written back |

## Surfaces

- `online_evaluate(store, config, metrics=)` → `OnlineReport` (`aggregate()`,
  `to_dict()`, `render()`).
- `OnlineEvaluator.run_once()` / `.run_forever(interval, stop=, max_batches=)`,
  with `on_report` and a lock so batches don't overlap.
- `create_online_eval_router(evaluator)` — FastAPI `POST /api/evals/run`,
  `GET /api/evals/report`, `GET /api/evals/summary` (lazy import).
- CLI `python -m reactifact eval <traces.db> [--sample 1.0] [--evaluators
  trace|judge|all] [--provider openai:gpt-4o-mini]`.

## Cost and duplicate control

Judges cost money: lower `sample_rate`, cap `limit`, wrap the provider in
`CachingLLM`, and bound spend with a `Quota`. The same run is only meaningful to
score once — annotate with tags and exclude already-tagged runs via `tags=[]` on
the next batch.
