"""reactifact.audit: answer provenance reports + reproducible state hashes."""

import asyncio

from pydantic import BaseModel
from reactifact import Consume, Context, Runtime, create_agent, produce
from reactifact.audit import (
    build_report,
    context_hash,
    data_hash,
    report_to_json,
    report_to_markdown,
)
from reactifact.sources import SourceRef


class Question(BaseModel):
    text: str


class Evidence(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


@produce(Evidence, also_creates=(SourceRef,))
async def gather(call):
    question = call.trigger
    if question is None or not isinstance(question.data, Question):
        return None
    ref = call.effects.create(
        SourceRef(source_id="docs", locator="doc.md", title="Doc"),
        id=f"ref:{question.id}",
    )
    call.effects.create(Evidence(text="E"), id=f"evidence:{question.id}").link(
        "extracted_from", ref
    )
    return None


@produce(Answer)
async def answer(call):
    evidence = next((a for a in call.inputs if isinstance(a.data, Evidence)), None)
    if evidence is None:
        return None
    call.effects.create(Answer(text="A"), id=f"answer:{evidence.id}").link(
        "supported_by", evidence
    )
    return None


def _build() -> Context:
    """A deterministic 3-artifact run with stable ids (reproducible hashes)."""
    ctx = Context()
    runtime = Runtime(
        ctx,
        agents=[
            create_agent("g", consumes=[Consume(Question)], produces=[gather]),
            create_agent("a", consumes=[Consume(Evidence)], produces=[answer]),
        ],
    )
    ctx.create(Question(text="q"), id="q")
    asyncio.run(runtime.arun())
    return ctx


# --- hashes ----------------------------------------------------------------- #


def test_data_hash_is_stable_and_content_sensitive():
    assert data_hash(Evidence(text="x")) == data_hash(Evidence(text="x"))
    assert data_hash(Evidence(text="x")) != data_hash(Evidence(text="y"))


def test_context_hash_is_reproducible_across_runs():
    assert context_hash(_build()) == context_hash(_build())


def test_context_hash_changes_when_state_changes():
    before = _build()
    after = _build()
    artifact = after.latest(Answer)
    assert artifact is not None
    after.update(artifact.id, Answer(text="changed"))
    assert context_hash(before) != context_hash(after)


# --- report ----------------------------------------------------------------- #


def test_report_walks_provenance_and_sources():
    ctx = _build()
    answer = ctx.latest(Answer)
    assert answer is not None

    report = build_report(ctx, answer)

    assert report.answer.artifact_id == answer.id
    assert report.answer.data_type == "Answer"
    types = {entry.data_type for entry in report.provenance}
    assert {"Evidence", "SourceRef"} <= types
    assert report.sources == ["doc.md"]
    assert ("", "", "") not in report.relations
    assert any(rel[1] == "supported_by" for rel in report.relations)
    assert any(rel[1] == "extracted_from" for rel in report.relations)
    assert report.context_sha256 == context_hash(ctx)


def test_report_producers_are_recorded():
    ctx = _build()
    answer = ctx.latest(Answer)
    assert answer is not None
    report = build_report(ctx, answer)
    # the answer was produced by an agent, so its author is non-empty
    assert report.answer.produced_by == "a"


def test_report_json_and_markdown_are_self_contained():
    ctx = _build()
    answer = ctx.latest(Answer)
    assert answer is not None
    report = build_report(ctx, answer, session_id="s1")

    as_json = report_to_json(report)
    assert report.context_sha256 in as_json
    assert '"data_type": "Answer"' in as_json

    as_md = report_to_markdown(report)
    assert "# Audit report" in as_md
    assert report.answer.sha256[:12] in as_md
    assert "doc.md" in as_md
