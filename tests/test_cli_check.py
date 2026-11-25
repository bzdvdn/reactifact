"""`reactifact check` — contracts, scenarios and dataset evals as one gate."""

from __future__ import annotations

import json

import pytest
from reactifact.cli import main

PIPELINE = "tests.cli_pipeline_fixture:pipeline"
SCENARIOS = "tests.fixtures.scenario_cases"


def _dataset(tmp_path, rows=None):
    path = tmp_path / "evals.json"
    path.write_text(
        json.dumps(
            {"examples": rows or [{"inputs": {"text": "hello"}}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(path)


def test_check_runs_all_three_stages(capsys, tmp_path):
    code = main(
        [
            "check",
            PIPELINE,
            "--scenarios",
            SCENARIOS,
            "-k",
            "passes",
            "--evals",
            _dataset(tmp_path),
            "--overall",
            "0",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "contracts  tests.cli_pipeline_fixture:pipeline: ok" in out
    assert "scenarios  tests.fixtures.scenario_cases" in out
    assert "PASS  fixture: passes" in out
    assert "evals" in out
    assert "answer_present" in out
    assert "check: PASSED" in out


def test_check_fails_a_scenario(capsys):
    code = main(["check", PIPELINE, "--scenarios", SCENARIOS, "-k", "fails"])
    out = capsys.readouterr().out

    assert code == 1
    assert "FAIL  fixture: fails" in out
    assert "check: FAILED" in out


def test_check_fails_a_contract(capsys):
    code = main(["check", "--contracts", "tests.cli_agents_fixture:GROUPS"])
    out = capsys.readouterr().out

    assert code == 1
    assert "contracts  tests.cli_agents_fixture:GROUPS: FAILED" in out
    assert "unknown_input" in out
    assert "check: FAILED" in out


def test_check_fails_a_gate(capsys, tmp_path):
    code = main(
        [
            "check",
            PIPELINE,
            "--evals",
            _dataset(tmp_path),
            "--gate",
            "answer_present=1.1",
        ]
    )
    out = capsys.readouterr().out

    assert code == 1
    assert "gate failed" in out
    assert "check: FAILED" in out


def test_check_requires_a_target_or_stage():
    with pytest.raises(SystemExit):
        main(["check"])


def test_check_evals_need_a_pipeline_target(tmp_path):
    with pytest.raises(SystemExit):
        main(["check", "--evals", _dataset(tmp_path)])
