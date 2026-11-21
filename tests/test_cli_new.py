"""`reactifact new` copies the bundled FastAPI template."""

from __future__ import annotations

from reactifact.cli import main


def test_new_app_creates_a_runnable_project(tmp_path):
    destination = tmp_path / "demo"
    assert main(["new", "app", str(destination)]) == 0

    assert (destination / "pyproject.toml").is_file()
    assert (destination / "app" / "main.py").is_file()
    assert (destination / "app" / "agent" / "prompts.py").is_file()
    assert (destination / ".env.example").is_file()
    assert (destination / "tests" / "conftest.py").is_file()
    assert (destination / "app" / "config.py").read_text().find("budget_max_runs") != -1


def test_new_app_refuses_a_non_empty_destination(tmp_path):
    destination = tmp_path / "busy"
    destination.mkdir()
    (destination / "keep.txt").write_text("do not overwrite")

    assert main(["new", "app", str(destination)]) == 1
    assert (destination / "keep.txt").read_text() == "do not overwrite"


def test_new_daemon_creates_a_worker_project(tmp_path):
    destination = tmp_path / "worker"
    assert main(["new", "daemon", str(destination)]) == 0

    assert (destination / "pyproject.toml").is_file()
    assert (destination / "app" / "main.py").is_file()
    assert (destination / "app" / "prompts.py").is_file()
    assert "run_once" in (destination / "app" / "config.py").read_text()
    assert "uvicorn" not in (destination / "README.md").read_text()
