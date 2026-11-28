"""`[tool.reactifact]` project configuration for the CLI.

The keys mirror the flags of `validate` / `graph --check` / `check`, so a
project can put its wiring in `pyproject.toml` once instead of repeating
`--seed`, `--terminal` and budget flags on every invocation:

    [tool.reactifact]
    agents = "app.pipeline:AGENTS"
    seeds = ["Question"]
    terminal = ["Answer"]
    scenarios = ["app.scenarios"]
    evals = "evals/qa.json"
    overall = 0.8

    [tool.reactifact.budget]
    max_runs = 80
    max_seconds = 120

Flags always win over the config; an absent section means empty defaults.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_BUDGET_KEYS = {
    "max_runs",
    "max_iterations",
    "max_seconds",
    "max_tool_calls",
    "max_tokens",
    "max_cost",
}


@dataclass(frozen=True)
class ProjectConfig:
    agents: str | None = None
    seeds: list[str] = field(default_factory=list)
    terminal: list[str] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)
    evals: str | None = None
    overall: float | None = None
    gates: dict[str, float] = field(default_factory=dict)
    budget: dict[str, int | float] = field(default_factory=dict)


def _find_pyproject(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _string_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit(f"[tool.reactifact] {key} must be a list of strings")
    return list(value)


def load_config(start: Path | None = None) -> ProjectConfig:
    """Reads `[tool.reactifact]` from the nearest `pyproject.toml`."""
    path = _find_pyproject((start or Path.cwd()).resolve())
    if path is None:
        return ProjectConfig()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw = data.get("tool", {}).get("reactifact", {})
    if not isinstance(raw, dict):
        raise SystemExit("[tool.reactifact] must be a table")
    budget = raw.get("budget", {})
    if not isinstance(budget, dict):
        raise SystemExit("[tool.reactifact] budget must be a table")
    unknown = set(budget) - _BUDGET_KEYS
    if unknown:
        raise SystemExit(
            "[tool.reactifact] unknown budget keys: " + ", ".join(sorted(unknown))
        )
    agents = raw.get("agents")
    if agents is not None and not isinstance(agents, str):
        raise SystemExit("[tool.reactifact] agents must be a module:attr string")
    evals = raw.get("evals")
    if evals is not None and not isinstance(evals, str):
        raise SystemExit("[tool.reactifact] evals must be a path string")
    overall = raw.get("overall")
    if overall is not None and not isinstance(overall, (int, float)):
        raise SystemExit("[tool.reactifact] overall must be a number")
    gates = raw.get("gates", {})
    if not isinstance(gates, dict) or not all(
        isinstance(key, str) and isinstance(value, (int, float))
        for key, value in gates.items()
    ):
        raise SystemExit("[tool.reactifact] gates must be a table of numbers")
    return ProjectConfig(
        agents=agents,
        seeds=_string_list(raw.get("seeds"), "seeds"),
        terminal=_string_list(raw.get("terminal"), "terminal"),
        scenarios=_string_list(raw.get("scenarios"), "scenarios"),
        evals=evals,
        overall=float(overall) if overall is not None else None,
        gates={key: float(value) for key, value in gates.items()},
        budget=dict(budget),
    )


def budget_from_config(config: ProjectConfig) -> Any:
    """Builds a `Budget` from the config table (None when unset)."""
    if not config.budget:
        return None
    from ..budget import Budget

    return Budget.model_validate(config.budget)


__all__ = ["ProjectConfig", "budget_from_config", "load_config"]
