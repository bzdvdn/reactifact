"""MemoryStore: namespaces, TTL, search and persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
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
