from __future__ import annotations

import asyncio
from pathlib import Path

from examples.tour.scenes import run_durable, run_model, run_reactive, run_state
from reactifact.providers import FakeLLM


def test_state_scene_versions_and_compaction() -> None:
    report = asyncio.run(run_state())

    assert report["current"] == "versioned artifacts!!"
    assert report["revision"] == 2
    assert report["versions"] == [0, 1, 2]
    assert report["historical_revision"] == 0
    assert report["changed"] == ["draft:1"]
    assert report["compaction_dropped"] >= 1
    assert report["versions_after_compaction"] == [1, 2]


def test_reactive_scene_debounces_fan_out() -> None:
    report = asyncio.run(run_reactive())

    assert report["digest_count"] == 1
    assert report["topic"] == "durability"
    assert report["max_facts"] == 3
    assert report["facts"] == [
        "typed inputs",
        "debounced fan-out",
        "scoped history",
    ]


def test_durable_scene_resumes_pending_event_and_replays(tmp_path: Path) -> None:
    report = asyncio.run(run_durable(tmp_path / "tour.sqlite3"))

    assert report["loaded"] is True
    assert report["done"] == "SURVIVE A RESTART"
    assert report["replayed_done"] == "SURVIVE A RESTART"
    assert report["version"] == 1


def test_model_scene_offline_fallback() -> None:
    report = asyncio.run(run_model(None))

    assert report["mode"] == "offline"
    assert report["brief"].startswith("Offline tour:")


def test_model_scene_uses_provider() -> None:
    report = asyncio.run(run_model(FakeLLM('{"text": "The model answered."}')))

    assert report["mode"] == "model"
    assert report["brief"] == "The model answered."
