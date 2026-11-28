"""MemoryStore: namespaces, TTL, search and persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from reactifact import (
    Context,
    Ensure,
    PendingAction,
    RuntimeResources,
)
from reactifact.checkpoints import FileKVBackend, InMemoryKVBackend
from reactifact.recipes.memory_store import MemoryStore


def test_put_get_update_and_list():
    backend = InMemoryKVBackend()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    memory = MemoryStore(backend, namespace="user:1", clock=lambda: now)

    async def go():
        first = await memory.put(
            "preference",
            {"tone": "concise"},
            text="prefers concise answers",
            tags={"kind": "preference"},
        )
        second = await memory.put("preference", {"tone": "warm"})
        loaded = await memory.get("preference")
        listed = await memory.list()
        return first, second, loaded, listed

    first, second, loaded, listed = asyncio.run(go())

    assert first.created_at == second.created_at  # replace preserves creation
    assert second.updated_at == first.updated_at
    assert loaded is not None
    assert loaded.value == {"tone": "warm"}
    assert loaded.text == "prefers concise answers"
    assert loaded.tags == {"kind": "preference"}
    assert [record.key for record in listed] == ["preference"]


def test_remember_and_recall_are_the_high_level_api():
    memory = MemoryStore(InMemoryKVBackend(), namespace="user:1")

    async def go():
        first = await memory.remember(
            "prefers concise answers with citations",
            value={"tone": "concise"},
            tags={"kind": "preference"},
        )
        second = await memory.remember(
            "prefers concise answers with citations",
            value={"tone": "warm"},
        )
        hits = await memory.recall("how should answers be written?", k=1)
        return first, second, hits

    first, second, hits = asyncio.run(go())

    assert first.key == second.key
    assert first.key.startswith("text:")
    assert second.value == {"tone": "warm"}
    assert second.tags == {"kind": "preference"}
    assert hits[0].record.key == first.key


def test_remember_accepts_a_pydantic_value_without_text():
    from pydantic import BaseModel

    class Preference(BaseModel):
        tone: str

    memory = MemoryStore(InMemoryKVBackend(), namespace="user:1")
    record = asyncio.run(memory.remember("", value=Preference(tone="warm")))

    assert record.value == {"tone": "warm"}
    assert record.text == '{"tone": "warm"}'


def test_namespaces_are_isolated():
    backend = InMemoryKVBackend()
    one = MemoryStore(backend, namespace="user:1")
    two = MemoryStore(backend, namespace="user:2")

    async def go():
        await one.put("secret", {"text": "only for one"}, text="only for one")
        return await one.get("secret"), await two.get("secret"), await two.search("one")

    first, other, hits = asyncio.run(go())

    assert first is not None
    assert other is None
    assert hits == []


def test_search_ranks_and_filters_tags():
    backend = InMemoryKVBackend()
    memory = MemoryStore(backend, namespace="team:acme")

    async def go():
        await memory.put(
            "refunds",
            {"text": "Refund policy requires manager approval"},
            tags={"team": "support"},
        )
        await memory.put(
            "shipping",
            {"text": "Shipping is handled by the logistics team"},
            tags={"team": "ops"},
        )
        all_hits = await memory.search("refund policy", k=5)
        filtered = await memory.search("team", k=5, tags={"team": "ops"})
        return all_hits, filtered

    hits, filtered = asyncio.run(go())

    assert hits
    assert hits[0].record.key == "refunds"
    assert filtered
    assert filtered[0].record.key == "shipping"


def test_ttl_expires_with_clock():
    backend = InMemoryKVBackend()
    current = [datetime(2026, 1, 1, tzinfo=UTC)]
    memory = MemoryStore(backend, namespace="user:1", clock=lambda: current[0])

    async def go():
        await memory.put("session", {"text": "temporary"}, ttl_seconds=10)
        before = await memory.get("session")
        current[0] += timedelta(seconds=11)
        after = await memory.get("session")
        listed = await memory.list()
        return before, after, listed

    before, after, listed = asyncio.run(go())

    assert before is not None
    assert after is None
    assert listed == []


def test_persists_over_file_backend_and_forgets(tmp_path):
    path = str(tmp_path / "memory")
    backend = FileKVBackend(path)
    first = MemoryStore(backend, namespace="user:1")

    assert asyncio.run(first.put("pref", {"text": "dark mode"})) is not None

    second = MemoryStore(FileKVBackend(path), namespace="user:1")
    loaded = asyncio.run(second.get("pref"))
    assert loaded is not None
    assert loaded.value == {"text": "dark mode"}

    assert asyncio.run(second.forget()) == 1
    assert asyncio.run(second.get("pref")) is None


def test_namespace_is_required():
    memory = MemoryStore(InMemoryKVBackend())

    with pytest.raises(ValueError, match="namespace"):
        asyncio.run(memory.put("x", {"text": "y"}))


def test_remember_from_records_provenance_and_forget_source():
    """A curated memory points back to its artifact; session erasure follows."""
    from pydantic import BaseModel
    from reactifact import Context

    class Report(BaseModel):
        text: str

    memory = MemoryStore(InMemoryKVBackend(), namespace="user:1")
    ctx = Context()
    report = ctx.create(Report(text="worker-2f1 OOMKilled in production"))

    record = asyncio.run(
        memory.remember_from(
            report,
            key=f"report:{report.id}",
            source_session="admin1:s1",
            tags={"kind": "report"},
        )
    )

    assert record.kind == "summary"
    assert record.text == "worker-2f1 OOMKilled in production"
    assert record.source_session == "admin1:s1"
    assert record.source_artifact == report.id
    assert record.source_version == report.version
    assert record.value == {"artifact": report.id, "data_type": report.data_type}

    assert asyncio.run(memory.forget_source("other-session")) == 0
    assert asyncio.run(memory.forget_source("admin1:s1")) == 1
    assert asyncio.run(memory.list()) == []


def test_remember_artifact_derives_a_provenance_key():
    from pydantic import BaseModel

    class Report(BaseModel):
        text: str

    memory = MemoryStore(InMemoryKVBackend(), namespace="user:1")
    report = Context().create(Report(text="worker-2f1 OOMKilled"))

    record = asyncio.run(memory.remember_artifact(report, source_session="admin1:s1"))

    assert record.key == f"report:{report.id}"
    assert record.source_artifact == report.id


def test_forget_source_rejects_an_empty_session():
    memory = MemoryStore(InMemoryKVBackend(), namespace="user:1")

    with pytest.raises(ValueError, match="non-empty source_session"):
        asyncio.run(memory.forget_source(""))
    with pytest.raises(ValueError, match="non-empty source_session"):
        asyncio.run(memory.forget_source("   "))


def test_remember_later_is_a_return_style_step_instruction():
    """The high-level spelling needs no Effects/Outbox at the call site."""
    from pydantic import BaseModel

    class Report(BaseModel):
        text: str

    memory = MemoryStore(InMemoryKVBackend(), namespace="user:1")
    report = Context().create(Report(text="worker-2f1 OOMKilled"))

    instruction = memory.remember_later(
        report,
        key=f"report:{report.id}",
        source_session="admin1:s1",
    )

    assert isinstance(instruction, Ensure)
    assert instruction.id == f"action:memory:report:{report.id}"
    assert isinstance(instruction.data, PendingAction)
    assert instruction.data.kind == "memory.remember"
    assert instruction.data.idempotency_key == f"memory:report:{report.id}"
    assert instruction.data.payload["source_artifact"] == report.id
    assert instruction.data.payload["source_session"] == "admin1:s1"


def test_memory_dispatch_indexes_a_committed_action():
    backend = InMemoryKVBackend()
    memory = MemoryStore(backend, namespace="user:1")
    resources = RuntimeResources()
    resources.register(MemoryStore, memory)
    ctx = Context(resources=resources)
    action = PendingAction(
        kind="memory.remember",
        idempotency_key="memory:report:1",
        payload={
            "key": "report:1",
            "text": "worker-2f1 OOMKilled",
            "kind": "summary",
            "tags": {"kind": "report"},
            "source_session": "admin1:s1",
            "source_artifact": "report:1",
            "source_version": 2,
            "data_type": "K8sReport",
        },
    )
    committed = ctx.create(action, id="action:memory:report:1")

    asyncio.run(MemoryStore.dispatch(ctx, committed))

    record = asyncio.run(memory.get("report:1"))
    assert record is not None
    assert record.text == "worker-2f1 OOMKilled"
    assert record.source_session == "admin1:s1"
    assert record.source_artifact == "report:1"
    assert record.source_version == 2
    assert record.tags == {"kind": "report"}


def test_memory_dispatch_ignores_other_action_kinds():
    ctx = Context(resources=RuntimeResources())
    action = PendingAction(kind="notify", idempotency_key="notify:1")
    committed = ctx.create(action, id="action:notify:1")

    asyncio.run(MemoryStore.dispatch(ctx, committed))  # no store needed


def test_records_written_before_provenance_still_load():
    """Records persisted by the first MemoryStore release load with defaults."""
    backend = InMemoryKVBackend()
    memory = MemoryStore(backend, namespace="user:1")
    asyncio.run(memory.put("pref", {"tone": "concise"}, text="concise"))

    async def strip_new_fields() -> None:
        key = (await backend.keys())[0]
        raw = await backend.get(key)
        for field in ("kind", "source_session", "source_artifact", "source_version"):
            raw.pop(field)
        await backend.set(key, raw)

    asyncio.run(strip_new_fields())
    loaded = asyncio.run(memory.get("pref"))
    assert loaded is not None
    assert loaded.text == "concise"
    assert loaded.kind == "fact"
    assert loaded.source_session == ""
    assert loaded.source_version is None
