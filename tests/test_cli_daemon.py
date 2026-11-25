"""`reactifact daemon` — one tick and trace setup."""

from __future__ import annotations

import pytest
from reactifact.cli import main


def test_daemon_run_once(capsys):
    code = main(
        [
            "daemon",
            "tests.cli_daemon_fixture:pipeline",
            "--seed",
            "tests.cli_daemon_fixture:next_seed",
            "--run-once",
        ]
    )

    assert code == 0
    out = capsys.readouterr()
    assert "daemon: 1 tick(s)" in out.out
    assert "trace:" in out.err


def test_daemon_run_once_with_no_trace(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "daemon",
            "tests.cli_daemon_fixture:pipeline",
            "--seed",
            "tests.cli_daemon_fixture:next_seed",
            "--run-once",
            "--no-trace",
        ]
    )

    assert code == 0
    assert "daemon: 1 tick(s)" in capsys.readouterr().out
    assert not (tmp_path / ".reactifact").exists()


def test_daemon_requires_a_seed():
    with pytest.raises(SystemExit):
        main(["daemon", "tests.cli_daemon_fixture:pipeline"])
