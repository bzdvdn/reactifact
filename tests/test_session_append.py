"""Append-only session persistence: snapshot + commit log, format versioning."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Context, SessionStore
from reactifact.checkpoints import SQLiteKVBackend
from reactifact.commit import Commit
from reactifact.context import CONTEXT_FORMAT_VERSION, migrate_context_payload
from reactifact.patches import Create


class Note(BaseModel):
    text: str


def _commit(context: Context, text: str, artifact_id: str) -> None:
    operation = Create(Note(text=text), id=artifact_id, artifact_id=artifact_id)
    context.apply_commit(
        Commit(author="test", message=f"add {text}", operations=[operation])
    )


def test_context_payload_has_a_format_version_and_legacy_loads():
    context = Context()
    _commit(context, "a", "note:1")

    payload = context.to_dict()
    assert payload["format_version"] == CONTEXT_FORMAT_VERSION

    legacy = dict(payload)
    legacy.pop("format_version")
    assert Context.from_dict(legacy).version == 1


def test_future_context_format_is_rejected():
    with pytest.raises(ValueError, match="newer than this reactifact"):
        migrate_context_payload({"format_version": CONTEXT_FORMAT_VERSION + 1})


def test_sqlite_session_appends_only_new_commits(tmp_path):
    async def _run() -> None:
        backend = SQLiteKVBackend(str(tmp_path / "sessions.db"))
        store = SessionStore(backend)
        context = Context()

        await store.save_session("s", context)  # snapshot at version 0
        _commit(context, "a", "note:1")
        await store.save_session("s", context)  # append one commit
        _commit(context, "b", "note:2")
        await store.save_session("s", context)  # append one more

        snapshot = await backend.get("s")
        assert snapshot is not None
        assert snapshot["format_version"] == CONTEXT_FORMAT_VERSION
        entries = await backend.entries("s")
        assert len(entries) == 2
        assert [len(entry["commits"]) for entry in entries] == [1, 1]

        loaded = await store.load_session("s")
        assert loaded is not None
        assert loaded.version == 2
        assert [note.data.text for note in loaded.list_artifacts(Note)] == ["a", "b"]
        await backend.aclose()

    asyncio.run(_run())


def test_append_persists_working_tree_artifacts(tmp_path):
    """User input is a working-tree artifact, not a commit: append saves must
    carry it too, or every message after the first is lost on reload."""

    async def _run() -> None:
        backend = SQLiteKVBackend(str(tmp_path / "sessions.db"))
        store = SessionStore(backend)
        context = Context()

        await store.save_session("s", context)  # initial snapshot
        first = context.create(Note(text="first user message"))
        _commit(context, "committed", "note:committed")
        await store.save_session("s", context)  # append

        second = context.create(Note(text="second user message"))
        await store.save_session("s", context)  # append again

        loaded = await store.load_session("s")
        assert loaded is not None
        texts = [note.data.text for note in loaded.list_artifacts(Note)]
        assert texts == ["first user message", "committed", "second user message"]
        assert loaded.get(first.id) is not None
        assert loaded.get(second.id) is not None
        await backend.aclose()

    asyncio.run(_run())


def test_working_tree_artifact_delete_survives_append_reload(tmp_path):
    async def _run() -> None:
        backend = SQLiteKVBackend(str(tmp_path / "sessions.db"))
        store = SessionStore(backend)
        context = Context()

        await store.save_session("s", context)
        first = context.create(Note(text="temporary"))
        await store.save_session("s", context)
        context.delete(first.id)
        await store.save_session("s", context)

        loaded = await store.load_session("s")
        assert loaded is not None
        assert loaded.list_artifacts(Note) == []
        await backend.aclose()

    asyncio.run(_run())


def test_compaction_rewrites_the_snapshot_and_clears_the_log(tmp_path):
    async def _run() -> None:
        backend = SQLiteKVBackend(str(tmp_path / "sessions.db"))
        store = SessionStore(backend)
        context = Context()

        await store.save_session("s", context)
        _commit(context, "a", "note:1")
        _commit(context, "b", "note:2")
        await store.save_session("s", context)
        assert len(await backend.entries("s")) == 1

        context.compact(keep_commits=0)
        await store.save_session("s", context)
        assert await backend.entries("s") == []

        loaded = await store.load_session("s")
        assert loaded is not None
        assert loaded.version == 2
        assert [note.data.text for note in loaded.list_artifacts(Note)] == ["a", "b"]
        await backend.aclose()

    asyncio.run(_run())


def test_append_log_restores_pending_events(tmp_path):
    async def _run() -> None:
        from reactifact.events import Event, EventType

        backend = SQLiteKVBackend(str(tmp_path / "sessions.db"))
        store = SessionStore(backend)
        context = Context()

        await store.save_session("s", context)
        _commit(context, "a", "note:1")
        context.replace_events([Event(EventType.ARTIFACT_CREATED, Note, "note:1")])
        await store.save_session("s", context)

        loaded = await store.load_session("s")
        assert loaded is not None
        assert [event.artifact_id for event in loaded.pending_events()] == ["note:1"]
        await backend.aclose()

    asyncio.run(_run())


def test_fresh_context_rewrites_an_existing_session_instead_of_appending(tmp_path):
    async def _run() -> None:
        backend = SQLiteKVBackend(str(tmp_path / "sessions.db"))
        store = SessionStore(backend)

        old = Context()
        _commit(old, "old", "note:old")
        await store.save_session("s", old)

        fresh = Context()
        _commit(fresh, "new", "note:new")
        await store.save_session("s", fresh)

        loaded = await store.load_session("s")
        assert loaded is not None
        assert [note.data.text for note in loaded.list_artifacts(Note)] == ["new"]
        await backend.aclose()

    asyncio.run(_run())


def test_compact_every_bounds_the_append_log(tmp_path):
    async def _run() -> None:
        backend = SQLiteKVBackend(str(tmp_path / "sessions.db"))
        store = SessionStore(backend, compact_every=3)
        context = Context()

        await store.save_session("s", context)
        for index in range(5):
            _commit(context, f"n{index}", f"note:{index}")
            await store.save_session("s", context)

        assert await backend.entry_count("s") < 3
        loaded = await store.load_session("s")
        assert loaded is not None
        assert [note.data.text for note in loaded.list_artifacts(Note)] == [
            "n0",
            "n1",
            "n2",
            "n3",
            "n4",
        ]
        await backend.aclose()

    asyncio.run(_run())


def test_compact_every_must_be_positive():
    with pytest.raises(ValueError, match="compact_every"):
        SessionStore(SQLiteKVBackend(":memory:"), compact_every=0)


def test_session_store_default_compact_every():
    from reactifact._persistence import DEFAULT_COMPACT_EVERY

    store = SessionStore(SQLiteKVBackend(":memory:"))
    assert store.compact_every == DEFAULT_COMPACT_EVERY


def test_branch_store_uses_the_append_log_per_branch(tmp_path):
    from reactifact.branching import BranchStore

    async def _run() -> None:
        backend = SQLiteKVBackend(str(tmp_path / "branches.db"))
        store = BranchStore(backend)
        context = Context()

        _commit(context, "a", "note:1")
        await store.save_branch(context, session_id="s", name="left")
        await store.save_branch(context, session_id="s", name="right")
        key_left = "branch:s:left"
        key_right = "branch:s:right"
        assert await backend.entry_count(key_left) == 0
        assert await backend.entry_count(key_right) == 0

        _commit(context, "b", "note:2")
        await store.save_branch(context, session_id="s", name="left")
        await store.save_branch(context, session_id="s", name="right")
        assert await backend.entry_count(key_left) == 1
        assert await backend.entry_count(key_right) == 1

        for name in ("left", "right"):
            loaded = await store.load_branch("s", name)
            assert loaded is not None
            assert [note.data.text for note in loaded.list_artifacts(Note)] == [
                "a",
                "b",
            ]
        await backend.aclose()

    asyncio.run(_run())


def test_legacy_v1_fixture_loads_and_migrates():
    import json
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "context_v1.json"
    context = Context.from_dict(json.loads(fixture.read_text()))

    assert context.version == 1
    artifact = context.get("question:legacy")
    assert artifact is not None
    assert artifact.data.text == "legacy question"
    assert context.to_dict()["format_version"] == CONTEXT_FORMAT_VERSION
