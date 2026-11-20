"""MemoryStore: namespaced, TTL-aware long-term memory over a KV backend."""

from __future__ import annotations

from typing import Any

from reactifact.checkpoints import InMemoryKVBackend
from reactifact.recipes import MemoryStore


async def run_memory_scene() -> dict[str, Any]:
    backend = InMemoryKVBackend()
    user = MemoryStore(backend, namespace="user:alice")
    team = MemoryStore(backend, namespace="team:acme")

    await user.put(
        "tone",
        {"value": "concise"},
        text="Alice prefers concise answers with citations",
        tags={"kind": "preference"},
    )
    await user.put(
        "timezone",
        {"value": "Europe/Berlin"},
        text="Alice is in the Berlin timezone",
        tags={"kind": "profile"},
    )
    await team.put(
        "escalation",
        {"value": "manager"},
        text="Support escalations go to the duty manager",
        tags={"kind": "policy"},
    )

    user_hits = await user.search("how should answers be written?", k=2)
    policy_hits = await team.search("who handles escalations?", k=1)
    other_hits = await user.search("escalations", k=5)
    return {
        "user_hits": [hit.record.key for hit in user_hits],
        "user_top": user_hits[0].record.value if user_hits else None,
        "team_hits": [hit.record.key for hit in policy_hits],
        "cross_namespace": [hit.record.key for hit in other_hits],
        "forgotten": await user.forget(),
    }
