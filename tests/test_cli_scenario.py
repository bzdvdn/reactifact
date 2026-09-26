"""`reactifact scenario` (`reactifact/cli/scenario.py`) — the CLI that runs
`reactifact.testing` scenarios. Complements `tests/test_cli.py` (which only
checks the subcommand is registered) and `tests/test_testing_lab.py` (which
covers `ScenarioLab` itself) with end-to-end coverage of the CLI's own
behavior: PASS/FAIL/ERROR/SKIP reporting, `-k` filtering, `--mode` plumbing,
and the "no scenarios found"/"unknown module" error paths — using the fixture
module in `tests/fixtures/scenario_cases.py` so this needs no real example,
model, or network call, and stays green on CI.
"""

from __future__ import annotations

import os

import pytest
from reactifact.cli import main
from reactifact.testing.record import MODE_ENV_VAR

FIXTURE_MODULE = "tests.fixtures.scenario_cases"


def test_runs_every_status_and_reports_a_summary(capsys):
    code = main(["scenario", FIXTURE_MODULE])
    out = capsys.readouterr().out

    assert code == 1  # FAILs and an ERROR are both present
    assert "PASS  fixture: passes" in out
    assert "FAIL  fixture: fails" in out
    assert "ERROR fixture: errors" in out
    assert "SKIP  fixture: skips" in out
    assert "no fixture configured" in out
    # pytest-style failure report: a FAILURES section, the failing frame from
    # the user's file with its source line, the exception, and the run state
    assert "FAILURES" in out
    assert "scenario_cases.py" in out
    assert 'assert 1 + 1 == 3, "math is broken"' in out
    assert "E   AssertionError: math is broken" in out
    assert "E   RuntimeError: boom" in out
    assert "cli/scenario.py" not in out  # framework frames are filtered
    assert "--- scenario state ---" in out  # "fails after a run" ran a ScenarioLab
    assert "scenario report" in out
    assert "6 scenario(s): 2 passed, 2 failed, 1 errored, 1 skipped" in out


def test_no_state_flag_suppresses_the_run_dump(capsys):
    code = main(["scenario", FIXTURE_MODULE, "-k", "fails after a run", "--no-state"])
    out = capsys.readouterr().out

    assert code == 1
    assert "AssertionFailure" in out
    assert "--- scenario state ---" not in out
    assert "scenario report" not in out


def test_filter_selects_a_single_scenario_by_substring(capsys):
    code = main(["scenario", FIXTURE_MODULE, "-k", "passes"])
    out = capsys.readouterr().out

    assert code == 0
    assert "PASS  fixture: passes" in out
    assert "fails" not in out
    assert "1 scenario(s): 1 passed, 0 failed, 0 errored, 0 skipped" in out


def test_exit_code_reflects_fail_or_error_presence(capsys):
    code = main(["scenario", FIXTURE_MODULE, "-k", "fixture:"])
    out = capsys.readouterr().out
    assert "6 scenario(s)" in out
    # code reflects the FAILs+ERROR present among all fixture scenarios
    assert code == 1

    code = main(["scenario", FIXTURE_MODULE, "-k", "passes"])
    assert code == 0


def test_filter_matching_nothing_reports_and_returns_one(capsys):
    code = main(["scenario", FIXTURE_MODULE, "-k", "no-such-substring"])
    out = capsys.readouterr().out

    assert code == 1
    assert "no scenarios found" in out


def test_unknown_module_raises_system_exit():
    with pytest.raises(SystemExit, match="could not import"):
        main(["scenario", "no.such.module"])


def test_mode_flag_sets_env_var_read_by_mode_from_env(capsys, monkeypatch):
    monkeypatch.delenv(MODE_ENV_VAR, raising=False)

    code = main(
        ["scenario", FIXTURE_MODULE, "-k", "reports its mode", "--mode", "replay"]
    )

    assert code == 0
    assert os.environ.get(MODE_ENV_VAR) == "replay"
    import tests.fixtures.scenario_cases as fixture_module

    assert fixture_module.last_seen_mode == "replay"


def test_without_mode_flag_leaves_env_var_untouched(capsys, monkeypatch):
    monkeypatch.delenv(MODE_ENV_VAR, raising=False)

    main(["scenario", FIXTURE_MODULE, "-k", "passes"])

    assert MODE_ENV_VAR not in os.environ
