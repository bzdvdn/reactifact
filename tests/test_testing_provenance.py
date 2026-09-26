"""`reactifact.testing` provenance assertions — `ArtifactAssertions.linked`
and `RelationAssertions` (the *edges*, not just the artifact fields)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from reactifact.context import Context
from reactifact.testing.assertions import ArtifactAssertions, RelationAssertions
from reactifact.testing.exceptions import AssertionFailure


class Evidence(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


def _ctx() -> tuple[Context, str, str]:
    ctx = Context()
    evidence = ctx.create(Evidence(text="refunds within 14 days"))
    answer = ctx.create(Answer(text="within 14 days"))
    ctx.link(answer.id, "supported_by", evidence.id)
    return ctx, answer.id, evidence.id


def test_linked_passes_and_returns_target_data():
    ctx, _, _ = _ctx()
    linked = ArtifactAssertions(ctx, Answer).linked("supported_by", Evidence)
    assert [data.text for data in linked] == ["refunds within 14 days"]


def test_linked_fails_when_relation_absent_and_inlines_links():
    ctx = Context()
    ctx.create(Answer(text="unbacked"))
    with pytest.raises(AssertionFailure, match="supported_by"):
        ArtifactAssertions(ctx, Answer).linked("supported_by", Evidence)


def test_linked_fails_when_no_artifact_of_the_type():
    ctx = Context()
    with pytest.raises(AssertionFailure, match="found none"):
        ArtifactAssertions(ctx, Answer).linked("supported_by")


def test_linked_type_filter_rejects_a_wrong_target_type():
    ctx = Context()
    answer = ctx.create(Answer(text="a"))
    evidence = ctx.create(Evidence(text="e"))
    ctx.link(answer.id, "mentions", evidence.id)
    with pytest.raises(AssertionFailure, match="Answer"):
        ArtifactAssertions(ctx, Answer).linked("mentions", Answer)


def test_links_measurement_returns_outgoing_relations():
    ctx, answer_id, evidence_id = _ctx()
    rels = ArtifactAssertions(ctx, Answer).links("supported_by")
    assert [(r.source_id, r.relation, r.target_id) for r in rels] == [
        (answer_id, "supported_by", evidence_id)
    ]


def test_relations_has_with_wildcards_and_helpers():
    ctx, answer_id, evidence_id = _ctx()
    relations = RelationAssertions(ctx)
    assert relations.has(relation="supported_by") == relations.all()
    assert relations.has(source=answer_id, target=evidence_id)[0].relation == (
        "supported_by"
    )
    assert relations.count(relation="supported_by") == 1
    assert relations.outgoing(answer_id) == relations.all()
    assert relations.incoming(evidence_id) == relations.all()


def test_relations_none_passes_when_absent_and_fails_when_present():
    ctx, _, _ = _ctx()
    relations = RelationAssertions(ctx)
    relations.none(relation="contradicted_by")  # absent -> passes
    with pytest.raises(AssertionFailure, match="supported_by"):
        relations.none(relation="supported_by")


def test_relations_has_failure_lists_present_edges():
    ctx = Context()
    answer = ctx.create(Answer(text="a"))
    evidence = ctx.create(Evidence(text="e"))
    ctx.link(answer.id, "mentions", evidence.id)
    with pytest.raises(AssertionFailure, match="mentions"):
        RelationAssertions(ctx).has(relation="supported_by")
