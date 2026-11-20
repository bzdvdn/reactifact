"""Branching & merge (§39-§40): fork, three-way conflict detection, BranchStore."""

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Context, MergeConflict, RuntimeResources
from reactifact.branching import BranchStore
from reactifact.checkpoints import SQLiteKVBackend


class Note(BaseModel):
    text: str


class Tag(BaseModel):
    label: str


def _base_context() -> Context:
    ctx = Context(resources=RuntimeResources())
    ctx.create(Note(text="base"), id="note:1")
    return ctx


def test_branch_isolation():
    parent = _base_context()
    fork = parent.branch(name="experiment")
    assert fork._fork_name == "experiment"

    parent.create(Note(text="parent-side"), id="note:2")
    fork.update("note:1", Note(text="fork-side"))

    assert len(parent.list_artifacts(Note)) == 2
    assert len(fork.list_artifacts(Note)) == 1
    assert fork.get("note:1").data.text == "fork-side"  # type: ignore[union-attr]
    assert parent.get("note:1").data.text == "base"  # type: ignore[union-attr]


def test_merge_union_of_divergent_creations():
    fork_a = _base_context().branch(name="a")
    fork_b = _base_context().branch(name="b")
    fork_a.create(Note(text="from-a"), id="added:a")
    fork_a.create(Tag(label="a-tag"), id="tag:a")
    fork_b.create(Note(text="from-b"), id="added:b")

    fork_a.merge(fork_b)

    assert fork_a.get("added:a").data.text == "from-a"  # type: ignore[union-attr]
    assert fork_a.get("added:b").data.text == "from-b"  # type: ignore[union-attr]
    assert fork_a.get("tag:a") is not None
    assert fork_a.get("note:1").data.text == "base"  # type: ignore[union-attr]
    assert fork_a.version == 1  # one merge commit applied


def test_merge_adopts_only_changed_side():
    fork_a = _base_context().branch(name="a")
    fork_b = _base_context().branch(name="b")
    fork_a.update("note:1", Note(text="changed-on-a"))

    fork_a.merge(fork_b)  # b is untouched → nothing to adopt
    assert fork_a.get("note:1").data.text == "changed-on-a"  # type: ignore[union-attr]


def test_merge_conflict_is_explicit_and_atomic():
    fork_a = _base_context().branch(name="a")
    fork_b = _base_context().branch(name="b")
    fork_a.update("note:1", Note(text="a-wins"))
    fork_b.update("note:1", Note(text="b-wins"))

    try:
        fork_a.merge(fork_b)
    except MergeConflict as exc:
        assert "note:1" in exc.conflicts[0]
    else:  # pragma: no cover
        raise AssertionError("expected MergeConflict")

    # atomic: nothing was applied
    assert fork_a.get("note:1").data.text == "a-wins"  # type: ignore[union-attr]


def test_merge_delete_conflict():
    fork_a = _base_context().branch(name="a")
    fork_b = _base_context().branch(name="b")
    fork_b.delete("note:1")
    fork_a.update("note:1", Note(text="edited"))

    try:
        fork_a.merge(fork_b)
    except MergeConflict:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected MergeConflict")


def test_merge_policy_resolves_a_same_type_conflict():
    fork_a = _base_context().branch(name="a")
    fork_b = _base_context().branch(name="b")
    fork_a.update("note:1", Note(text="a-wins"))
    fork_b.update("note:1", Note(text="b-wins"))

    fork_a.merge(
        fork_b,
        policies={Note: lambda left, right: Note(text=f"{left.text}+{right.text}")},
    )

    assert fork_a.get("note:1").data.text == "a-wins+b-wins"  # type: ignore[union-attr]


def test_merge_policy_wrong_type_still_fails():
    fork_a = _base_context().branch(name="a")
    fork_b = _base_context().branch(name="b")
    fork_a.update("note:1", Note(text="a-wins"))
    fork_b.update("note:1", Note(text="b-wins"))

    with pytest.raises(TypeError, match="merge policy"):
        fork_a.merge(fork_b, policies={Note: lambda left, right: Tag(label="wrong")})


def test_merge_clean_delete_propagates():
    fork_a = _base_context().branch(name="a")
    fork_b = _base_context().branch(name="b")
    fork_b.delete("note:1")
    fork_a.merge(fork_b)
    assert fork_a.get("note:1") is None


def test_merge_unifies_relations():
    fork_a = _base_context().branch(name="a")
    fork_b = _base_context().branch(name="b")
    fork_a.create(Note(text="x"), id="rel-src")
    fork_b.create(Note(text="y"), id="rel-dst")
    fork_b.link("rel-dst", "supported_by", "note:1")
    fork_a.merge(fork_b)
    assert len(fork_a.relations()) == 1


def test_branch_store_roundtrip_persists_fork_base(tmp_path):
    asyncio.run(_test_branch_store_roundtrip_persists_fork_base(tmp_path))


async def _test_branch_store_roundtrip_persists_fork_base(tmp_path):
    backend = SQLiteKVBackend(str(tmp_path / "branches.sqlite3"))
    store = BranchStore(backend)
    fork = _base_context().branch(name="hypo-a")
    fork.create(Note(text="finding"), id="finding:1")

    await store.save_branch(fork, session_id="demo", name="hypo-a")
    restored = await store.load_branch("demo", "hypo-a")
    assert restored is not None
    assert restored.get("finding:1").data.text == "finding"  # type: ignore[union-attr]
    assert restored._base is not None  # fork base survived serialization
    assert await store.list_branches("demo") == ["hypo-a"]
    await store.delete_branch("demo", "hypo-a")
    assert await store.load_branch("demo", "hypo-a") is None


def test_branch_merge_works_after_persistence(tmp_path):
    asyncio.run(_test_branch_merge_works_after_persistence(tmp_path))


async def _test_branch_merge_works_after_persistence(tmp_path):
    backend = SQLiteKVBackend(str(tmp_path / "branches.sqlite3"))
    store = BranchStore(backend)

    base = _base_context()
    a = base.branch(name="a")
    b = base.branch(name="b")
    a.create(Note(text="a-finding"), id="f:a")
    b.create(Note(text="b-finding"), id="f:b")
    await store.save_branch(a, session_id="demo", name="a")
    await store.save_branch(b, session_id="demo", name="b")

    a2 = await store.load_branch("demo", "a")
    b2 = await store.load_branch("demo", "b")
    assert a2 is not None and b2 is not None
    a2.merge(b2)  # three-way against the persisted fork base — no conflict
    assert a2.get("f:a") is not None
    assert a2.get("f:b") is not None
    assert a2._base is not None
