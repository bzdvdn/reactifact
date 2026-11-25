"""`reactifact doctor` checks settings and common app boundaries."""

from __future__ import annotations

from types import SimpleNamespace

from reactifact.cli.doctor import check_settings


def _settings(**overrides):
    values = {
        "env": "dev",
        "auth_token": "test",
        "session_backend": "sqlite",
        "trace_backend": "sqlite",
        "llm_provider": "none",
        "llm_model": None,
        "llm_api_key_env": None,
        "budget_max_runs": 10,
        "budget_max_iterations": 10,
        "budget_max_seconds": 10,
        "budget_max_tool_calls": 2,
        "budget_max_tokens": None,
        "budget_max_cost": None,
        "chat_memory_keep": 20,
        "chat_memory_commits": 100,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_offline_settings_are_ok(tmp_path):
    (tmp_path / "app.py").write_text(
        "def lifespan():\n    pass\nscope_session = None\n"
    )
    findings = check_settings(_settings(), root=tmp_path)
    assert not [f for f in findings if f.level == "ERROR"]


def test_prod_rejects_memory_sessions(tmp_path):
    findings = check_settings(
        _settings(env="prod", session_backend="memory"), root=tmp_path
    )
    assert any(
        f.level == "ERROR" and "session_backend=memory" in f.message for f in findings
    )


def test_missing_auth_and_model_are_errors(tmp_path):
    findings = check_settings(
        _settings(auth_token=None, llm_provider="deepseek", llm_model=None),
        root=tmp_path,
    )
    messages = [f.message for f in findings if f.level == "ERROR"]
    assert any("auth token" in m for m in messages)
    assert any("llm_model" in m for m in messages)


def test_budget_and_removed_usage_are_warnings(tmp_path):
    (tmp_path / "app.py").write_text("@produce\nfrom reactifact import Produce\n")
    findings = check_settings(
        _settings(
            budget_max_runs=None,
            budget_max_iterations=None,
            budget_max_seconds=None,
            budget_max_tool_calls=None,
        ),
        root=tmp_path,
    )
    messages = [f.message for f in findings if f.level == "WARN"]
    assert any("no budget" in m for m in messages)
    assert any("removed" in m for m in messages)
