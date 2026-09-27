"""The `examples.chat` demo — a minimal single-model multi-turn conversation,
bounded by `ChatMemory` (offline, no LLM)."""

from __future__ import annotations

import asyncio

from examples.chat.main import KEEP, run


def test_chat_example_is_bounded_and_renders_the_window():
    data = asyncio.run(run())

    assert data["kept"] <= KEEP
    transcript = data["transcript"]
    assert transcript
    assert all(role in ("user", "assistant") for role, _ in transcript)
    assert transcript[-1][1].startswith("[echo]")
