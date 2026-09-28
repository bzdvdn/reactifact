"""Online evaluation over a trace store (§56)."""

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    FakeLLM,
    Produce,
    Runtime,
    RuntimeResources,
    create_agent,
)
from reactifact.cli import main
from reactifact.eval import (
    OnlineEvalConfig,
    OnlineEvaluator,
    answer_present,
    context_source,
    from_metric,
    judge_relevance,
    no_errors,
    online_evaluate,
    output_present,
    trajectory_match,
)
from reactifact.metrics import Metrics
from reactifact.tracing import Tracer, TraceStore


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str
    sources: list[str] = []


class Answerer(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call):
        call.effects.create(Answer(text=call.trigger.data.text.upper()))


def _agent():
    return create_agent("answerer", consumes=[Consume(Question)], produces=[Answerer()])


def _run_once() -> Context:
    ctx = Context(resources=RuntimeResources())
    ctx.create(Question(text="hello"))
    asyncio.run(Runtime(ctx, agents=[_agent()]).arun())
    return ctx


def _store_with_runs(tmp_path, count=3) -> TraceStore:
    """Runs the pipeline `count` times, each exported to a fresh `TraceStore`."""
    store = TraceStore(str(tmp_path / "traces.db"))
    for _ in range(count):
        ctx = Context(resources=RuntimeResources())
        ctx.create(Question(text="hello"))
        asyncio.run(Runtime(ctx, agents=[_agent()], tracer=Tracer(store=store)).arun())
    return store


def _instrumented_store(tmp_path, count=3):
    """Alias kept for readability at the call sites below."""
    return _store_with_runs(tmp_path, count)


# --------------------------------------------------------------------------- #
# trace source + metrics
# --------------------------------------------------------------------------- #


def test_online_evaluate_scores_trace_runs(tmp_path):
    store = _instrumented_store(tmp_path, count=3)
    report = asyncio.run(
        online_evaluate(
            store,
            OnlineEvalConfig(
                evaluators={
                    "present": output_present(),
                    "clean": no_errors(),
                },
                sample_rate=1.0,
                annotate=False,
            ),
        )
    )
    assert report.sampled == 3
    assert report.evaluated == 3
    assert report.overall() == 1.0
    assert report.aggregate() == {"present": 1.0, "clean": 1.0}


def test_sampling_is_seeded_and_reproducible(tmp_path):
    store = _instrumented_store(tmp_path, count=10)
    config = OnlineEvalConfig(
        evaluators={"present": output_present()},
        sample_rate=0.5,
        seed=42,
        annotate=False,
    )
    first = asyncio.run(online_evaluate(store, config))
    second = asyncio.run(online_evaluate(store, config))
    assert first.sampled == second.sampled
    assert [r.case for r in first.report.results] == [
        r.case for r in second.report.results
    ]


def test_head_strategy_takes_all_newest(tmp_path):
    store = _instrumented_store(tmp_path, count=4)
    report = asyncio.run(
        online_evaluate(
            store,
            OnlineEvalConfig(
                evaluators={"present": output_present()},
                strategy="head",
                limit=2,
                annotate=False,
            ),
        )
    )
    assert report.sampled == 2


def test_trajectory_metric_with_reference_fn(tmp_path):
    store = _instrumented_store(tmp_path, count=1)
    report = asyncio.run(
        online_evaluate(
            store,
            OnlineEvalConfig(
                evaluators={"path": trajectory_match("strict", steps="agents")},
                sample_rate=1.0,
                reference_fn=lambda run: {"trajectory": ["answerer"]},
                annotate=False,
            ),
        )
    )
    assert report.aggregate() == {"trajectory_strict_match": 1.0}


# --------------------------------------------------------------------------- #
# context rehydration + judge
# --------------------------------------------------------------------------- #


def test_context_source_rehydrates(tmp_path):
    store = _instrumented_store(tmp_path, count=1)

    def run_fn(run_id):
        return _run_once()

    report = asyncio.run(
        online_evaluate(
            store,
            OnlineEvalConfig(
                evaluators={"present": from_metric("answer_present", answer_present)},
                sample_rate=1.0,
                source=context_source(run_fn),
                annotate=False,
            ),
        )
    )
    assert report.overall() == 1.0


def test_judge_over_traces_with_fake_llm(tmp_path):
    store = _instrumented_store(tmp_path, count=1)
    report = asyncio.run(
        online_evaluate(
            store,
            OnlineEvalConfig(
                evaluators={
                    "relevance": judge_relevance(FakeLLM('{"score": true}')),
                },
                sample_rate=1.0,
                annotate=False,
            ),
        )
    )
    assert report.aggregate() == {"relevance": 1.0}


# --------------------------------------------------------------------------- #
# annotations + metrics + scheduler
# --------------------------------------------------------------------------- #


def test_annotations_are_written(tmp_path):
    store = _instrumented_store(tmp_path, count=2)
    report = asyncio.run(
        online_evaluate(
            store,
            OnlineEvalConfig(
                evaluators={"present": output_present()},
                sample_rate=1.0,
                tag="eval",
            ),
        )
    )
    assert report.annotated == 2
    tagged = asyncio.run(store.query(tags=["eval"]))
    assert tagged["total"] == 2


def test_failure_tag_on_low_scores(tmp_path):
    store = _instrumented_store(tmp_path, count=2)

    def always_zero(eval_input):
        return 0.0

    report = asyncio.run(
        online_evaluate(
            store,
            OnlineEvalConfig(evaluators={"zero": always_zero}, sample_rate=1.0),
        )
    )
    assert report.failed == 2
    failed = asyncio.run(store.query(tags=["eval:failed"]))
    assert failed["total"] == 2


def test_metrics_are_recorded(tmp_path):
    store = _instrumented_store(tmp_path, count=2)
    metrics = Metrics()
    asyncio.run(
        online_evaluate(
            store,
            OnlineEvalConfig(
                evaluators={"present": output_present()},
                sample_rate=1.0,
                annotate=False,
            ),
            metrics=metrics,
        )
    )
    text = metrics.render()
    assert "reactifact_online_eval_runs_total" in text
    assert 'result="pass"' in text
    assert "reactifact_online_eval_score" in text


def test_scheduler_run_once_and_run_forever(tmp_path):
    store = _instrumented_store(tmp_path, count=2)
    evaluator = OnlineEvaluator(
        store,
        OnlineEvalConfig(
            evaluators={"present": output_present()},
            sample_rate=1.0,
            annotate=False,
        ),
    )
    report = asyncio.run(evaluator.run_once())
    assert report.evaluated == 2
    assert evaluator.last_report is report

    seen = []
    evaluator2 = OnlineEvaluator(
        store,
        OnlineEvalConfig(
            evaluators={"present": output_present()},
            sample_rate=1.0,
            annotate=False,
        ),
        on_report=seen.append,
    )
    asyncio.run(evaluator2.run_forever(0.01, max_batches=2))
    assert len(seen) == 2


# --------------------------------------------------------------------------- #
# CLI + web router
# --------------------------------------------------------------------------- #


def test_cli_eval(tmp_path, capsys):
    _instrumented_store(tmp_path, count=2)
    assert main(["eval", str(tmp_path / "traces.db"), "--sample", "1.0"]) == 0
    out = capsys.readouterr().out
    assert "online eval" in out
    assert "output_present" in out


def test_cli_eval_judge_needs_provider(tmp_path):
    _instrumented_store(tmp_path, count=1)
    with pytest.raises(SystemExit):
        main(["eval", str(tmp_path / "traces.db"), "--evaluators", "judge"])


def test_online_eval_router(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from reactifact.eval import create_online_eval_router

    store = _instrumented_store(tmp_path, count=2)
    evaluator = OnlineEvaluator(
        store,
        OnlineEvalConfig(
            evaluators={"present": output_present()},
            sample_rate=1.0,
            annotate=False,
        ),
    )
    app = FastAPI()
    app.include_router(create_online_eval_router(evaluator))
    client = TestClient(app)

    assert client.get("/api/evals/report").status_code == 404
    run = client.post("/api/evals/run").json()
    assert run["evaluated"] == 2
    summary = client.get("/api/evals/summary").json()
    assert summary["aggregate"] == {"present": 1.0}
