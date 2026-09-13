"""medic-lab: hypotheses → channels → evaluation → human steer → report.

Hermetic: the evidence pool is the local fixture pages (no network).
"""

import asyncio
from pathlib import Path

from examples.medic_lab.agents import (
    InvestigatorAgent,
    medic_lab_agents,
    medic_lab_scheduler,
)
from examples.medic_lab.models import (
    Claim,
    Hypothesis,
    Question,
    ResearchReport,
    SearchDone,
)
from reactifact import Budget, Context, Runtime, RuntimeResources
from reactifact.sources import FileSystemSource

PAGES = Path(__file__).resolve().parents[1] / "examples" / "medic_lab" / "pages"
QUESTION = "Does vitamin D supplementation prevent colds?"


def build():
    resources = RuntimeResources(
        llm=None,
        sources={"papers": FileSystemSource(str(PAGES), source_id="papers")},
    )
    ctx = Context(resources=resources)
    runtime = Runtime(
        ctx, agents=medic_lab_agents(), budget=Budget(max_runs=400), max_concurrency=4
    )
    return ctx, runtime


def run_until_report(ctx: Context, runtime: Runtime, answers: list[str]) -> Question:
    """Runs the lab, answering every steering question from `answers`."""
    from reactifact.interrupt import PendingQuestion

    question = ctx.create(Question(text=QUESTION, session_id="test"))
    guard = 0
    while guard < 80:
        asyncio.run(runtime.arun())
        if ctx.list_artifacts(ResearchReport):
            return question
        pending = ctx.latest_pending_question()
        if pending is not None and isinstance(pending.data, PendingQuestion):
            if not answers:
                ctx.resume(pending.id, "stop")
            else:
                ctx.resume(pending.id, answers.pop(0))
        guard += 1
    raise AssertionError("the lab did not produce a report")


def test_lab_produces_report_with_ranking():
    ctx, runtime = build()
    question_id = run_until_report(ctx, runtime, []).id

    reports = [r for r in ctx.list_artifacts(ResearchReport)]
    assert len(reports) == 1
    report = reports[0].data
    assert report.question_id == question_id
    assert len(report.ranking) == 4
    assert report.ranking == sorted(report.ranking, key=lambda r: r.score, reverse=True)
    assert report.answer.strip()
    assert report.uncertainty.strip()


def test_support_and_contradiction_links_exist():
    ctx, runtime = build()
    run_until_report(ctx, runtime, [])

    hypotheses = {h.id: h for h in ctx.list_artifacts(Hypothesis)}
    assert len(hypotheses) == 4
    any_support = any(ctx.incoming(hid, relation="supports") for hid in hypotheses)
    any_contra = any(ctx.incoming(hid, relation="contradicts") for hid in hypotheses)
    assert any_support, "at least one hypothesis must be supported by evidence"
    assert any_contra, "at least one hypothesis must be contradicted"


def test_medic_lab_scheduler_prioritizes_more_contradicted_hypothesis():
    """`medic_lab_scheduler()` (§26) wires `relation_balance_metric` in —
    when two open hypotheses are both eligible for (re-)investigation, the
    more-contradicted one must be scheduled first, using the same
    `supports`/`contradicts` relations `Evaluator` scores by hand
    (`produce/evaluate.py`)."""
    resources = RuntimeResources(llm=None)
    ctx = Context(resources=resources)
    steady = ctx.create(Hypothesis(question_id="q", statement="steady"))
    shaky = ctx.create(Hypothesis(question_id="q", statement="shaky"))
    support = ctx.create(Hypothesis(question_id="q", statement="support-src"))
    against1 = ctx.create(Hypothesis(question_id="q", statement="against1"))
    against2 = ctx.create(Hypothesis(question_id="q", statement="against2"))
    ctx.link(support.id, "supports", steady.id)
    ctx.link(against1.id, "contradicts", shaky.id)
    ctx.link(against2.id, "contradicts", shaky.id)

    by_id = {e.artifact_id: e for e in ctx.drain_events()}
    investigator = InvestigatorAgent()
    steady_candidate = (investigator, by_id[steady.id], [])
    shaky_candidate = (investigator, by_id[shaky.id], [])

    out = asyncio.run(medic_lab_scheduler()(ctx, [steady_candidate, shaky_candidate]))
    assert out == [shaky_candidate, steady_candidate]


def test_cross_hypothesis_contradiction():
    ctx, runtime = build()
    run_until_report(ctx, runtime, [])

    links = [
        rel
        for c in ctx.list_artifacts(Claim)
        for rel in ctx.relations(source_id=c.id, relation="contradicted_by")
    ]
    claim_ids = {c.id for c in ctx.list_artifacts(Claim)}
    assert links, "cross-hypothesis contradictions (claim-level) expected"
    assert all(rel.target_id in claim_ids for rel in links)


def test_deepen_reopens_hypothesis_before_report():
    ctx, runtime = build()
    run_until_report(ctx, runtime, ["H3", "stop"])

    question = ctx.list_artifacts(Question)[0].data
    assert question.depth >= 1  # one deepening round happened
    rounds = {s.data.round for s in ctx.list_artifacts(SearchDone)}
    assert rounds >= {0, 1}
    report = ctx.list_artifacts(ResearchReport)[0].data
    assert report.ranking  # report still produced after the deepening


def test_human_can_deepen_by_plain_number():
    ctx, runtime = build()
    run_until_report(ctx, runtime, ["2", "stop"])
    assert ctx.list_artifacts(ResearchReport)[0].data.ranking


def test_topic_agnostic_hypotheses_from_llm(tmp_path):
    """With an LLM the lab works for any topic: hypotheses come from the model,
    polarity from the page content — not from file names."""
    from reactifact.providers import LLMProvider, LLMRequest, LLMResponse

    class StubLLM(LLMProvider):
        text = (
            '{"text": "caffeine blocks adenosine and reduces deep sleep.", '
            '"hypotheses": ['
            '"coffee reduces sleep quality", '
            '"coffee has no meaningful effect on sleep", '
            '"coffee affects only sensitive individuals", '
            '"the evidence on coffee and sleep is mixed"]}'
        )

        async def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(text=self.text)

        async def stream(self, request):
            yield LLMResponse(text="")

    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "pro_caffeine.md").write_text(
        "# caffeine and sleep\ncaffeine blocks adenosine, delays sleep onset and "
        "shortens deep sleep, and impairs sleep quality in most people.",
        encoding="utf-8",
    )
    (pages / "contra_caffeine.md").write_text(
        "# caffeine tolerance\nhabitual caffeine consumers show no meaningful "
        "change in total sleep time; the effect mostly evaporates with tolerance.",
        encoding="utf-8",
    )

    resources = RuntimeResources(
        llm=StubLLM(),
        sources={"papers": FileSystemSource(str(pages), source_id="papers")},
    )
    ctx = Context(resources=resources)
    runtime = Runtime(
        ctx, agents=medic_lab_agents(), budget=Budget(max_runs=400), max_concurrency=4
    )
    run_until_report(ctx, runtime, ["stop"])

    statements = [h.data.statement for h in ctx.list_artifacts(Hypothesis)]
    assert any("coffee" in s.lower() for s in statements), "LLM hypotheses used"
    assert not any("vitamin D" in s for s in statements), "no hardcoded topic"
    report = ctx.list_artifacts(ResearchReport)[0].data
    assert report.ranking
    # content polarity produced real scores (not all zeros)
    assert any(r.score != 0 for r in report.ranking)


def test_deepen_uses_model_queries_and_report_synthesis(tmp_path):
    """With an LLM, deepening asks the model for clarifying sub-questions (used
    by the next investigation round) and the reporter synthesizes the answer."""
    from reactifact.providers import LLMProvider, LLMRequest, LLMResponse

    class StubLLM(LLMProvider):
        text = (
            '{"text": "SYNTHESIZED_LAB_ANSWER", '
            '"hypotheses": ["coffee reduces sleep quality", "coffee has no effect '
            'on sleep", "coffee affects only sensitive individuals", '
            '"the evidence on coffee and sleep is mixed"], '
            '"questions": ["does caffeine tolerance explain the null findings?", '
            '"are objective sleep measures used?"]}'
        )

        async def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(text=self.text)

        async def stream(self, request):
            yield LLMResponse(text="")

    from examples.medic_lab.models import Question

    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "pro_caffeine.md").write_text(
        "# caffeine and sleep\ncaffeine delays sleep onset and impairs sleep quality.",
        encoding="utf-8",
    )
    (pages / "contra_caffeine.md").write_text(
        "# caffeine tolerance\nhabitual caffeine consumers show no meaningful "
        "change in total sleep time due to tolerance.",
        encoding="utf-8",
    )

    ctx = Context(
        resources=RuntimeResources(
            llm=StubLLM(),
            sources={"papers": FileSystemSource(str(pages), source_id="papers")},
        )
    )
    runtime = Runtime(
        ctx, agents=medic_lab_agents(), budget=Budget(max_runs=400), max_concurrency=4
    )
    run_until_report(ctx, runtime, ["1", "stop"])

    question = ctx.list_artifacts(Question)[0].data
    assert question.depth >= 1
    # the model proposed clarifying questions for the deepened hypothesis
    deepened_hyp = ctx.list_artifacts(Hypothesis)[1]  # index 1 → answered "1"
    assert question.deepen_queries.get(deepened_hyp.id)
    # the reporter synthesized the answer from the model
    report = ctx.list_artifacts(ResearchReport)[0].data
    assert report.answer == "SYNTHESIZED_LAB_ANSWER"
