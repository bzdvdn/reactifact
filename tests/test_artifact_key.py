"""Typed artifact keys: one stable id scheme for Ensure/Read/cleanup."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from reactifact import (
    Artifact,
    ArtifactKey,
    Context,
    Ensure,
    Read,
    RuntimeResources,
    Step,
)


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    query_id: str
    text: str


class Digest(BaseModel):
    text: str = ""


AnswerKey = ArtifactKey(Answer, prefix="answer", field="query_id")
DigestKey = ArtifactKey(Digest, prefix="digest")


def test_key_builds_a_stable_id_string():
    assert AnswerKey("q1") == "answer:q1"
    assert AnswerKey("q1", "v2") == "answer:q1:v2"
    assert DigestKey() == "digest"
    assert str(DigestKey) == "digest"


def test_key_derives_the_target_from_an_artifact():
    context = Context(resources=RuntimeResources())
    answer = context.create(Answer(query_id="q1", text="ok"), id=AnswerKey("q1"))

    assert AnswerKey.from_artifact(answer) == "answer:q1"
    assert context.get(AnswerKey("q1")) is answer


def test_singleton_key_derives_its_prefix():
    artifact = Artifact(data=Digest(text="old"))
    assert DigestKey.from_artifact(artifact) == "digest"


def test_key_returns_none_when_the_field_is_missing():
    class NoQuery(BaseModel):
        text: str

    key = ArtifactKey(NoQuery, prefix="no-query", field="query_id")
    artifact = Artifact(data=NoQuery(text="x"))
    assert key.from_artifact(artifact) is None


def test_key_rejects_invalid_configuration():
    with pytest.raises(ValueError, match="prefix"):
        ArtifactKey(Answer, prefix="")
    with pytest.raises(ValueError, match="prefix"):
        ArtifactKey(Answer, prefix="a:b")
    with pytest.raises(ValueError, match="not both"):
        ArtifactKey(
            Answer,
            prefix="answer",
            field="query_id",
            id_of=lambda artifact: "x",
        )


def test_ensure_accepts_a_bound_key():
    ensure = Ensure(Answer(query_id="q1", text="ok"), id=AnswerKey("q1"))
    assert ensure.id == "answer:q1"


def test_read_idempotent_accepts_an_artifact_key():
    class Write(Step[Answer]):
        reads = (Read.idempotent(AnswerKey),)

        async def run(self, question: Question) -> Answer:
            return Answer(query_id="q1", text=question.text)

    step = Write()
    specs = step.wake_specs()
    assert specs is not None
    consume = specs[0]
    assert consume.artifact_type is Answer
    assert consume.id_of is not None
    artifact = Artifact(data=Answer(query_id="q1", text="ok"))
    assert consume.id_of(artifact) == "answer:q1"
