"""`[tool.reactifact]` project configuration."""

from __future__ import annotations

import pytest
from reactifact.cli.config import load_config


def test_config_reads_groups_seeds_terminal_budget(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.reactifact]
groups = "app.pipeline:GROUPS"
seeds = ["Question"]
terminal = ["Answer"]
scenarios = ["app.scenarios"]
evals = "evals/qa.json"
overall = 0.8

[tool.reactifact.gates]
faithfulness = 0.9

[tool.reactifact.budget]
max_runs = 80
max_seconds = 120
""".strip()
    )
    config = load_config(tmp_path)
    assert config.groups == "app.pipeline:GROUPS"
    assert config.seeds == ["Question"]
    assert config.terminal == ["Answer"]
    assert config.scenarios == ["app.scenarios"]
    assert config.evals == "evals/qa.json"
    assert config.overall == 0.8
    assert config.gates == {"faithfulness": 0.9}
    assert config.budget == {"max_runs": 80, "max_seconds": 120}


def test_config_is_empty_without_the_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    config = load_config(tmp_path)
    assert config.groups is None
    assert config.seeds == []


def test_config_rejects_unknown_budget_keys(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.reactifact.budget]\nmax_agents = 3\n"
    )
    with pytest.raises(SystemExit, match="unknown budget"):
        load_config(tmp_path)
