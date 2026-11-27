import asyncio
import logging
from pathlib import Path

from examples.knowledge.agents import (
    AnswerBuilder,
    Calculator,
    EvidenceBuilder,
    Planner,
    ProgressEvaluator,
    Resolver,
    SearchScout,
    TableResolver,
    Verifier,
)
from examples.knowledge.models import (
    Answer,
    Calculation,
    ChatReply,
    Claim,
    Evidence,
    ResearchTurn,
    Spreadsheet,
    UserQuery,
)
from reactifact import (
    Budget,
    Context,
    Runtime,
    RuntimeResources,
)
from reactifact.providers import LLMProvider, LLMResponseChunk
from reactifact.recipes import keyword_score
from reactifact.sources import CSVSource, FileSystemSource

DOCS = Path(__file__).resolve().parents[1] / "examples" / "knowledge" / "docs"


def build_runtime(extra_sources=None, replace=False):
    default = {
        "guide": FileSystemSource(
            str(DOCS / "guide"), source_id="guide", scorer=keyword_score
        ),
        "pricing": FileSystemSource(
            str(DOCS / "pricing"), source_id="pricing", scorer=keyword_score
        ),
    }
    if extra_sources is not None and not replace:
        default.update(extra_sources)
    elif extra_sources is not None:
        default = dict(extra_sources)
    resources = RuntimeResources(llm=None, sources=default)
    ctx = Context(resources=resources)
    runtime = Runtime(
        ctx,
        agents=[
            Planner,
            SearchScout,
            Resolver,
            TableResolver,
            EvidenceBuilder,
            Verifier,
            Calculator,
            ProgressEvaluator,
            AnswerBuilder,
        ],
        budget=Budget(max_runs=80),
    )
    return ctx, runtime


def test_greeting_short_circuits():
    ctx, runtime = build_runtime()
    query = ctx.create(UserQuery(text="hello"))
    asyncio.run(runtime.arun())

    replies = [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == query.id]
    assert replies
    assert replies[-1].data.kind == "greeting"
    assert ctx.list_artifacts(Answer) == []


def test_research_builds_answer_with_sources():
    ctx, runtime = build_runtime()
    query = ctx.create(UserQuery(text="how to set up authentication?"))
    asyncio.run(runtime.arun())

    answers = [a for a in ctx.list_artifacts(Answer) if a.data.query_id == query.id]
    assert len(answers) == 1
    answer = answers[0].data
    assert "guide:auth.md" in answer.sources
    assert answer.text.strip()


def test_answer_has_provenance_chain():
    """Provenance chain (§34): Answer →supported_by→ Evidence →extracted_from→ Doc
    →resolved_from→ SourceRef, without query_id strings as relations."""
    ctx, runtime = build_runtime()
    query = ctx.create(UserQuery(text="how to set up authentication?"))
    asyncio.run(runtime.arun())

    answer = next(a for a in ctx.list_artifacts(Answer) if a.data.query_id == query.id)
    evidences = ctx.related(answer.id, relation="supported_by")
    assert len(evidences) >= 1
    for evidence in evidences:
        docs = ctx.related(evidence.id, relation="extracted_from")
        assert len(docs) == 1
        refs = ctx.related(docs[0].id, relation="resolved_from")
        assert len(refs) == 1
        assert refs[0].data.locator == docs[0].data.path
    # every fact has a supporting source: claim (derived_from) and/or an answer
    assert len(ctx.incoming(evidences[0].id, relation="supported_by")) == 1
    assert ctx.incoming(evidences[0].id, relation="derived_from")


def test_per_query_scoping():
    ctx, runtime = build_runtime()

    q1 = ctx.create(UserQuery(text="how much does GPU inference cost?"))
    asyncio.run(runtime.arun())

    q2 = ctx.create(UserQuery(text="how to set up authentication?"))
    asyncio.run(runtime.arun())

    a1 = [a for a in ctx.list_artifacts(Answer) if a.data.query_id == q1.id]
    a2 = [a for a in ctx.list_artifacts(Answer) if a.data.query_id == q2.id]
    assert a1 and a2
    assert a1[0].data.sources == ["pricing:tiers.md"]
    assert a2[0].data.sources == ["guide:auth.md"]


def test_stream_yields_research_statuses():
    ctx, runtime = build_runtime()
    ctx.create(UserQuery(text="how to set up authentication?"))

    async def collect():
        return [ev async for ev in runtime.astream()]

    events = asyncio.run(collect())

    statuses = [e.message for e in events if e.kind == "status"]
    assert any("Searching for information" in m for m in statuses)
    assert any("Found" in m for m in statuses)
    assert any("Assembling the answer" in m for m in statuses)
    assert events[-1].kind == "run_end"


def test_turn_lifecycle_reaches_answered():
    ctx, runtime = build_runtime()
    query = ctx.create(UserQuery(text="how to set up authentication?"))
    asyncio.run(runtime.arun())

    turn = [t for t in ctx.list_artifacts(ResearchTurn) if t.data.query_id == query.id][
        0
    ]
    assert turn.data.status == "answered"


def test_turn_insufficient_when_nothing_found():
    empty = DOCS / "empty_dir"
    empty.mkdir(exist_ok=True)
    ctx, runtime = build_runtime(
        {"empty": FileSystemSource(str(empty), source_id="empty")}, replace=True
    )

    query = ctx.create(UserQuery(text="why is the server down?"))
    asyncio.run(runtime.arun())

    turns = [t for t in ctx.list_artifacts(ResearchTurn) if t.data.query_id == query.id]
    assert len(turns) == 1
    assert turns[0].data.status == "insufficient"
    assert ctx.list_artifacts(Answer) == []


def test_multiturn_no_runaway_cascade():
    """Several questions in one session: each completes with its own answer,
    without an endless scout→resolver→evidence→evaluator loop (§42, §24).

    Regression: a repeated scout run (create-or-refresh refs → ARTIFACT_UPDATED)
    was stopped only by the budget, and mid-session questions got no answer.
    """
    ctx, runtime = build_runtime()
    for text, expected in (
        ("what methods does the REST API have?", "api.md"),
        ("how to install the platform?", "install.md"),
        ("how to pay invoices?", "billing.md"),
        ("how to set up authentication?", "auth.md"),
    ):
        query = ctx.create(UserQuery(text=text))
        asyncio.run(runtime.arun())
        answers = [a for a in ctx.list_artifacts(Answer) if a.data.query_id == query.id]
        assert len(answers) == 1, f"no answer for {text!r}"
        assert any(expected in s for s in answers[0].data.sources)
        assert runtime.last_stats.outcome.value == "completed"


# ---- Agent 5: verification of claims (§35, §36) ----


def test_verification_produces_claims_with_provenance():
    """Evidence → Claim: confidence, status, and the Claim→Evidence→Doc chain."""
    ctx, runtime = build_runtime()
    query = ctx.create(UserQuery(text="how to set up authentication?"))
    asyncio.run(runtime.arun())

    claims = [c for c in ctx.list_artifacts(Claim) if c.data.query_id == query.id]
    assert claims, "Verifier must build claims from the facts"
    for claim in claims:
        assert 0.0 <= claim.data.confidence <= 1.0
        assert claim.data.status in {"verified", "weak", "unverified"}
        evs = ctx.related(claim.id, relation="derived_from")
        assert len(evs) == 1, "every claim — derived_from→ Evidence"
        assert type(evs[0].data) is Evidence
        docs = ctx.related(evs[0].id, relation="extracted_from")
        assert len(docs) == 1


def test_claims_ranked_into_answer():
    """BuildAnswer uses claims (higher-awareness, §68): the answer is non-empty
    and backed by claims."""
    ctx, runtime = build_runtime()
    query = ctx.create(UserQuery(text="how to install the platform?"))
    asyncio.run(runtime.arun())

    answer = next(a for a in ctx.list_artifacts(Answer) if a.data.query_id == query.id)
    assert answer.data.text.strip()
    supported = ctx.related(answer.id, relation="supported_by")
    assert any(type(a.data) is Claim for a in supported) or supported


# ---- Agent 6: structured data (§29, §67) ----


def test_csv_source_preserves_structure():
    src = CSVSource(str(DOCS / "costs"), source_id="costs")
    refs = src.search("how much does GPU inference cost?")
    assert len(refs) == 1
    assert refs[0].metadata.get("structured") is True

    import asyncio as _asyncio

    payload = _asyncio.run(src.resolve(refs[0]))
    assert payload["columns"] == ["service", "month", "cost usd", "gpu cost usd"]
    assert len(payload["rows"]) == 4
    assert ["inference", "2024-05", "2150", "1700"] in payload["rows"]


def test_calculation_from_csv_source():
    """A sum question → deterministic calculation (not hallucination), §29/§67."""
    ctx, runtime = build_runtime(
        {"costs": CSVSource(str(DOCS / "costs"), source_id="costs")}
    )
    query = ctx.create(UserQuery(text="how much does GPU inference cost in total?"))
    asyncio.run(runtime.arun())

    calcs = [c for c in ctx.list_artifacts(Calculation) if c.data.query_id == query.id]
    assert len(calcs) == 1
    calc = calcs[0].data
    assert calc.value == 5480 or calc.value == 3580, calc.value
    sheets = ctx.related(calcs[0].id, relation="derived_from")
    assert len(sheets) == 1
    assert type(sheets[0].data) is Spreadsheet
    # Spreadsheet carries structure (schema+rows), not text (§29)
    assert sheets[0].data.columns == ["service", "month", "cost usd", "gpu cost usd"]
    assert len(sheets[0].data.rows) == 4
    # Answer is also built with the calculation: the source is a table
    answer = next(a for a in ctx.list_artifacts(Answer) if a.data.query_id == query.id)
    assert any("costs:" in s for s in answer.data.sources)


def test_non_calculation_question_ignores_table():
    """A document question does not trigger a calculation even with a CSV source."""
    ctx, runtime = build_runtime(
        {"costs": CSVSource(str(DOCS / "costs"), source_id="costs")}
    )
    query = ctx.create(UserQuery(text="how to set up authentication?"))
    asyncio.run(runtime.arun())

    assert ctx.list_artifacts(Calculation) == []
    answer = next(a for a in ctx.list_artifacts(Answer) if a.data.query_id == query.id)
    assert "guide:auth.md" in answer.data.sources


# ---- Agent 7: reliability — LLM provider outage (structured_llm on_error) ----


class AlwaysFailsLLM(LLMProvider):
    """Configured but unreachable — distinct from `llm=None` ("no provider")."""

    async def complete(self, request):
        raise RuntimeError("simulated provider outage")

    async def stream(self, request):
        yield LLMResponseChunk(text="")  # pragma: no cover — not exercised here


def test_answer_degrades_honestly_when_llm_provider_fails(caplog):
    """A relevant question with evidence, but the LLM provider is down (not
    just unconfigured): the turn still reaches "answered" from raw evidence
    (ExtractEvidence's own honest fallback), BuildAnswer's `on_error` logs the
    outage instead of swallowing it, and the answer text is non-empty and
    sourced — the pipeline degrades honestly rather than stalling.

    Regression for a diagnosis mix-up: an *unrelated* question (no matching
    evidence at all) was mistaken for a cascading-failure bug caused by the
    LLM outage, when the two are unconnected — this test pins the actual
    LLM-outage path down so it can't regress silently.
    """
    ctx, runtime = build_runtime()
    ctx.resources.llm = AlwaysFailsLLM()
    query = ctx.create(UserQuery(text="how to set up authentication?"))

    with caplog.at_level(logging.WARNING):
        asyncio.run(runtime.arun())

    turn = next(
        t for t in ctx.list_artifacts(ResearchTurn) if t.data.query_id == query.id
    )
    assert turn.data.status == "answered"
    answer = next(a for a in ctx.list_artifacts(Answer) if a.data.query_id == query.id)
    assert answer.data.text.strip()
    assert "guide:auth.md" in answer.data.sources
    assert any(
        "BuildAnswer: LLM provider call failed" in r.message for r in caplog.records
    )


def test_unrelated_question_is_insufficient_regardless_of_llm():
    """The counterpart to the above: a question with *no* matching evidence
    goes "insufficient" the same way whether the LLM is up, down, or absent —
    it is never reached, so an LLM outage cannot be the cause of that path."""
    ctx, runtime = build_runtime()
    ctx.resources.llm = AlwaysFailsLLM()
    query = ctx.create(UserQuery(text="what is the weather today?"))
    asyncio.run(runtime.arun())

    turn = next(
        t for t in ctx.list_artifacts(ResearchTurn) if t.data.query_id == query.id
    )
    assert turn.data.status == "insufficient"
    assert ctx.list_artifacts(Answer) == []
