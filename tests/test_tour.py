from __future__ import annotations

import asyncio
from pathlib import Path

from examples.tour.scenes import (
    run_conflicts,
    run_durable,
    run_events,
    run_experiment_scene,
    run_hitl,
    run_map_reduce_scene,
    run_memory_scene,
    run_model,
    run_reactive,
    run_replay_scene,
    run_resilience,
    run_state,
    run_subflow_scene,
    run_swarm_scene,
    run_time_travel,
    run_validate_scene,
)
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


def test_resilience_scene_retries_and_times_out() -> None:
    report = asyncio.run(run_resilience())

    assert report["attempts"] == 2
    assert report["answer"] == "RETRY ME"
    assert report["timed_out"] is True
    assert report["artifacts_after_timeout"] == 0


def test_hitl_scene_uses_typed_resume() -> None:
    report = asyncio.run(run_hitl())

    assert report["schema_present"] is True
    assert report["answered"] is True
    assert report["approved"] is True
    assert report["note"] == "within policy"
    assert report["final"] == "approved: within policy"


def test_time_travel_scene_snapshots_and_forks() -> None:
    report = asyncio.run(run_time_travel())

    assert report["versions"] == [1, 2]
    assert report["first_author"] == "finish"
    assert report["live"] == "SECOND"
    assert report["snapshot"] == "FIRST"
    assert report["fork_version"] == 1
    assert report["fork"] == "FIRST"


def test_events_scene_streams_typed_events() -> None:
    report = asyncio.run(run_events())

    assert report["has_start"] is True
    assert report["first"] == "run_start"
    assert report["last"] == "run_end"
    assert report["delta"] == "echo"
    assert report["streamed"] is True
    assert report["writes"] == 1
    assert report["value_added"] == 1
    assert report["tool_starts"] == 1
    assert report["custom"] == "calling the model…"
    assert report["outcome"] == "completed"


def test_conflicts_scene_is_atomic_then_merge_policy() -> None:
    report = asyncio.run(run_conflicts())

    assert report["conflict_raised"] is True
    assert report["conflict_actual"] == 1
    assert report["artifact_after"] == "base"
    assert report["merge_conflict"] is True
    assert report["merged"] == "left+right"


def test_subflow_scene_links_child_trace_and_provenance() -> None:
    report = asyncio.run(run_subflow_scene())

    assert report["final"] == "HELLO FROM SUBFLOW"
    assert report["child_run_id"]
    assert report["parent_run_id"]
    assert report["same_trace_link"] is True
    assert report["child_outputs"] == 1
    assert report["relations"] == 1


def test_map_reduce_scene_fans_out_and_reduces() -> None:
    report = asyncio.run(run_map_reduce_scene())

    assert report["tasks"] == 3
    assert report["results"] == 3
    assert report["failures"] == 0
    assert report["reduced"] == "ALPHA | BETA | GAMMA"
    assert report["version"] >= 2


def test_swarm_scene_hands_off_with_provenance() -> None:
    report = asyncio.run(run_swarm_scene())

    assert report["final"] == "billing handled: refund please"
    assert report["hops"] == 1
    assert report["from"] == "triage"
    assert report["to"] == "billing"
    assert report["run_ids"] == 2
    assert report["relations"] == 2


def test_replay_scene_reuses_the_recording() -> None:
    report = asyncio.run(run_replay_scene())

    assert report["recorded"] == "replay me"
    assert report["replayed"] == "replay me"
    assert report["same"] is True
    assert report["recording_lines"] == 1


def test_experiment_scene_picks_upper_variant() -> None:
    report = asyncio.run(run_experiment_scene())

    assert report["winner"] == "upper"
    assert report["rows"][0]["quality"] == 1.0
    assert report["rows"][1]["quality"] == 0.0


def test_validate_scene_finds_missing_producer() -> None:
    report = run_validate_scene()

    assert report["ok"] is True
    assert report["warnings"] == 0
    assert "unknown_input" in report["bad_codes"]
    assert "error" in report["bad_render"]


def test_memory_scene_is_namespaced_and_searchable() -> None:
    report = asyncio.run(run_memory_scene())

    assert report["user_top"] == {"value": "concise"}
    assert report["user_hits"][0] == "tone"
    assert report["team_hits"] == ["escalation"]
    assert report["cross_namespace"] == []
    assert report["forgotten"] == 2
