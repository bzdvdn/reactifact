"""`Context.why()` / `Context.sources()` — provenance as a query, not a BFS."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Context


class Doc(BaseModel):
    text: str
    locator: str


class Evidence(BaseModel):
    quote: str


class Answer(BaseModel):
    text: str


def chain() -> tuple[Context, str]:
    context = Context()
    context.create(Doc(text="policy", locator="policy.md"), id="doc")
    context.create(Evidence(quote="10%"), id="evidence")
    context.link("evidence", "extracted_from", "doc")
    context.create(Answer(text="over budget"), id="answer")
    context.link("answer", "supported_by", "evidence")
    return context, "answer"


def test_why_walks_edges_breadth_first():
    context, answer = chain()
    links = context.why(answer)
    assert [(link.relation, link.artifact.id, link.depth) for link in links] == [
        ("supported_by", "evidence", 1),
        ("extracted_from", "doc", 2),
    ]


def test_why_can_filter_relations_and_depth():
    context, answer = chain()
    assert [
        link.artifact.id for link in context.why(answer, relations=("supported_by",))
    ] == ["evidence"]
    assert [link.artifact.id for link in context.why(answer, max_depth=1)] == [
        "evidence"
    ]


def test_sources_returns_deduplicated_locators():
    context, answer = chain()
    assert context.sources(answer) == ["policy.md"]


def test_sources_includes_the_root_when_it_has_a_locator():
    context = Context()
    doc = context.create(Doc(text="policy", locator="policy.md"), id="doc")
    assert context.sources(doc) == ["policy.md"]
    assert context.sources("doc") == ["policy.md"]


def test_why_on_a_missing_id_is_empty():
    context, _ = chain()
    assert context.why("missing") == []
    assert context.sources("missing") == []
