"""Eval harness (§56): multi-level metrics over the final state, deterministic."""

import asyncio
from pathlib import Path

from pydantic import BaseModel
from reactifact import Context, RuntimeResources
from reactifact.eval import (
    EvalCase,
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


class Answer(BaseModel):
    query_id: str
    text: str
    sources: list[str] = []


class Evidence(BaseModel):
    query_id: str
    source: str = ""
    text: str = ""
    score: float = 0.5


class Claim(BaseModel):
    query_id: str
    text: str = ""
    confidence: float = 0.0
    status: str = "unverified"


class Calculation(BaseModel):
    query_id: str
    value: float = 0.0


def _golden() -> Context:
    ctx = Context(resources=RuntimeResources())
    ev = ctx.create(Evidence(query_id="q1", source="doc:1", text="fact", score=0.9))
    claim = ctx.create(
        Claim(query_id="q1", text="claim", confidence=0.8, status="verified")
    )
    answer = ctx.create(
        Answer(query_id="q1", text="the total is 42", sources=["doc:1"])
    )
    ctx.link(answer.id, "supported_by", ev.id)
    ctx.link(claim.id, "derived_from", ev.id)
    ctx.create(Calculation(query_id="q1", value=42.0), id="calc:1")
    return ctx


def test_core_metrics_on_golden_state():
    context = _golden()
    assert answer_present(context) == 1.0
    assert provenance_grounded(context) == 1.0
    assert evidence_quality(context) == 1.0
    assert claim_verification(context) == 1.0


def test_failures_lower_scores():
    ctx = Context(resources=RuntimeResources())
    weak = ctx.create(Evidence(query_id="q", score=0.2), id="e:lo")
    claim = ctx.create(Claim(query_id="q", status="weak"), id="c:1")
    ctx.create(Answer(query_id="q", text="x", sources=["nope"]), id="a:1")
    ctx.link(claim.id, "derived_from", weak.id)

    assert answer_present(ctx) == 1.0
    assert provenance_grounded(ctx) == 0.0
    assert evidence_quality(ctx) == 0.0
    assert claim_verification(ctx) == 0.0


def test_generative_metrics_with_ground_truth():
    context = _golden()
    expected = {
        "answer": "the total is 42",
        "calculations": [42.0],
        "sources": ["doc:"],
    }

    metrics = {
        "answer_coverage": answer_coverage(),
        "calc": calculation_correctness(),
        "sources": source_coverage(),
    }
    assert metrics["answer_coverage"](context, expected) == 1.0
    assert metrics["calc"](context, expected) == 1.0
    assert metrics["sources"](context, expected) == 1.0

    bad = {
        "answer": "completely different",
        "calculations": [999.0],
        "sources": ["other:"],
    }
    assert metrics["answer_coverage"](context, bad) < 1.0
    assert metrics["calc"](context, bad) == 0.0


def test_confidence_calibration_scores():
    ctx = Context(resources=RuntimeResources())
    ctx.create(Claim(query_id="q", text="a", confidence=0.95), id="c:right-high")
    ctx.create(Claim(query_id="q", text="b", confidence=0.05), id="c:wrong-low")

    metric = confidence_calibration()
    well_calibrated = metric(
        ctx, {"claim_correctness": {"c:right-high": True, "c:wrong-low": False}}
    )
    assert well_calibrated is not None
    assert well_calibrated > 0.9

    # same claims, ground truth flipped: confidence now points the wrong way
    badly_calibrated = metric(
        ctx, {"claim_correctness": {"c:right-high": False, "c:wrong-low": True}}
    )
    assert badly_calibrated is not None
    assert badly_calibrated < 0.1

    assert metric(ctx, None) is None
    assert metric(ctx, {}) is None


def test_skipped_metrics_when_no_ground_truth():
    context = _golden()
    result = run_suite(
        [EvalCase(name="no-truth", run=lambda: context)],
        {"answer_coverage": answer_coverage()},
    ).results[0]
    assert result.skipped == ["answer_coverage"]
    assert result.overall() == 0.0


def test_report_render_and_dict():
    context = _golden()
    report = run_suite(
        [EvalCase(name="golden", run=lambda: context)],
        {"answer_present": answer_present, "provenance_grounded": provenance_grounded},
    )
    assert report.results[0].overall() == 1.0
    assert report.overall() == 1.0
    rendered = report.render()
    assert "[golden]" in rendered
    assert "provenance_grounded" in rendered
    d = report.to_dict()
    assert d["overall"] == 1.0
    assert d["cases"][0]["case"] == "golden"


# --------------------------------------------------------------------------- #
# End-to-end: evaluate the knowledge calculation question offline (§29, §67)
# --------------------------------------------------------------------------- #


def _run_knowledge_calc() -> Context:
    from examples.knowledge.models import UserQuery
    from examples.knowledge.step_groups import (
        AnswerBuilder,
        CalculatorGroup,
        EvidenceBuilder,
        Planner,
        ProgressEvaluator,
        ResolverGroup,
        SearchScout,
        TableResolver,
        VerifierGroup,
    )
    from reactifact import Budget, Runtime
    from reactifact.recipes import keyword_score
    from reactifact.sources import CSVSource, FileSystemSource

    DOCS = Path(__file__).resolve().parents[1] / "examples" / "knowledge" / "docs"
    resources = RuntimeResources(
        llm=None,
        sources={
            "guide": FileSystemSource(
                str(DOCS / "guide"), source_id="guide", scorer=keyword_score
            ),
            "pricing": FileSystemSource(
                str(DOCS / "pricing"), source_id="pricing", scorer=keyword_score
            ),
            "costs": CSVSource(str(DOCS / "costs"), source_id="costs"),
        },
    )
    ctx = Context(resources=resources)
    runtime = Runtime(
        ctx,
        agents=[
            Planner(),
            SearchScout(),
            ResolverGroup(),
            TableResolver(),
            EvidenceBuilder(),
            VerifierGroup(),
            CalculatorGroup(),
            ProgressEvaluator(),
            AnswerBuilder(),
        ],
        budget=Budget(max_runs=80),
    )
    ctx.create(UserQuery(text="how much does GPU inference cost in total?"))
    asyncio.run(runtime.arun())
    return ctx


def test_end_to_end_knowledge_calc_scores():
    case = EvalCase(
        name="knowledge-calc",
        run=_run_knowledge_calc,
        expected={"sources": ["costs:", "pricing:", "guide:"]},
    )
    metrics = {
        **core_metrics,
        "calc": calculation_correctness(values=(5480.0, 3580.0)),
        "sources": source_coverage(),
    }
    result = run_case(case, metrics)
    by_name = {m.name: m.score for m in result.metrics}
    assert by_name["answer_present"] == 1.0
    assert by_name["provenance_grounded"] == 1.0
    assert by_name["calc"] == 1.0
    assert by_name["sources"] == 1.0
    assert result.overall() > 0.9
    assert result.skipped == []
