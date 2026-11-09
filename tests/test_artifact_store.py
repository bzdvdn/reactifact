from dataclasses import FrozenInstanceError

from pydantic import BaseModel
from reactifact import Context
from reactifact.artifacts import artifact_content_hash


class Doc(BaseModel):
    text: str


def test_store_tracks_versions_by_content_hash():
    ctx = Context()
    doc = ctx.create(Doc(text="one"))

    history = ctx.version_history(doc.id)
    assert [v.data.text for v in history] == ["one"]
    assert history[0].content_hash == artifact_content_hash(Doc(text="one"))

    ctx.update(doc.id, Doc(text="two"))
    history = ctx.version_history(doc.id)
    assert [v.version for v in history] == [0, 1]
    assert [v.data.text for v in history] == ["one", "two"]

    found = ctx.find_versions(artifact_content_hash(Doc(text="one")))
    assert [(v.artifact_id, v.version) for v in found] == [(doc.id, 0)]


def test_store_skips_noop_updates():
    ctx = Context()
    doc = ctx.create(Doc(text="same"))

    ctx.update(doc.id, Doc(text="same"))

    assert [v.version for v in ctx.version_history(doc.id)] == [0]


def test_deleted_artifact_history_is_still_readable():
    ctx = Context()
    doc = ctx.create(Doc(text="one"))
    ctx.update(doc.id, Doc(text="two"))
    ctx.delete(doc.id)

    assert ctx.get(doc.id) is None
    assert [v.data.text for v in ctx.version_history(doc.id)] == ["one", "two"]
    assert ctx.artifact_version(doc.id, 1).data.text == "two"


def test_find_versions_can_filter_by_stable_type_id():
    ctx = Context()
    doc = ctx.create(Doc(text="same"))
    record = ctx.artifact_version(doc.id, 0)
    assert record is not None

    assert ctx.find_versions(record.content_hash, data_type=record.data_type)
    assert not ctx.find_versions(record.content_hash, data_type="nope")


def test_compaction_trims_versions_authoritatively():
    ctx = Context()
    doc = ctx.create(Doc(text="one"))
    ctx.update(doc.id, Doc(text="two"))
    ctx.update(doc.id, Doc(text="three"))

    ctx.compact(keep_commits=ctx.version, keep_versions=1)

    assert ctx.artifact_version(doc.id, 0) is None
    assert [v.version for v in ctx.version_history(doc.id)] == [1, 2]


def test_artifact_handles_are_frozen_snapshots():
    ctx = Context()
    first = ctx.create(Doc(text="one"))
    second = ctx.update(first.id, Doc(text="two"))
    assert second is not None

    assert first.version == 0
    assert first.data.text == "one"
    assert second.version == 1
    assert second.data.text == "two"
    assert ctx.get(first.id) is not None
    assert ctx.get(first.id).data.text == "two"

    import pytest

    with pytest.raises(FrozenInstanceError):
        first.data = Doc(text="mutated")


def test_snapshot_preserves_absolute_artifact_versions():
    ctx = Context()
    doc = ctx.create(Doc(text="one"))
    ctx.update(doc.id, Doc(text="two"))
    ctx.update(doc.id, Doc(text="three"))

    snapshot = ctx.at(0)

    current = snapshot.get(doc.id)
    assert current is not None
    assert current.version == 2
    assert [v.data.text for v in snapshot.version_history(doc.id)] == [
        "one",
        "two",
        "three",
    ]
