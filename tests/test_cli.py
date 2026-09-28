"""`reactifact/cli/*` — end-to-end smoke coverage for every subcommand.

Extracted from a single 334-line `__main__.py` into one module per subcommand
(§0.4.0 changelog); this module was the one part of the release shipped with
no test at all. Covers: parser wiring, `graph` against a real example module,
`context`/`replay`/`branch` happy paths over a real `SessionStore`, `trace`
against a real `TraceStore`, and the "store not found" error paths every
subcommand shares via `common.open_store`.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Context
from reactifact.checkpoints import FileKVBackend, SQLiteKVBackend
from reactifact.cli import build_parser, main
from reactifact.cli.common import load_agents, open_store
from reactifact.session import SessionStore
from reactifact.tracing import RunTrace, TraceStore


class Doc(BaseModel):
    text: str


def run(coro):
    return asyncio.run(coro)


def test_build_parser_registers_every_subcommand():
    parser = build_parser()
    sub_actions = [
        a
        for a in parser._subparsers._group_actions
        if a.dest == "command"  # type: ignore[union-attr]
    ]
    choices = sub_actions[0].choices
    assert choices is not None
    assert set(choices) == {
        "graph",
        "context",
        "trace",
        "replay",
        "branch",
        "scenario",
        "skills",
        "eval",
    }


def test_main_no_command_prints_help_and_returns_zero(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "reactifact" in out
    assert "graph" in out


def test_open_store_picks_backend_by_extension(tmp_path):
    assert isinstance(open_store(str(tmp_path / "s.sqlite3"), "auto"), SQLiteKVBackend)
    assert isinstance(open_store(str(tmp_path / "s.db"), "auto"), SQLiteKVBackend)
    assert isinstance(open_store(str(tmp_path / "sessions"), "auto"), FileKVBackend)
    assert isinstance(open_store(str(tmp_path / "sessions"), "sqlite"), SQLiteKVBackend)


def test_load_agents_synthesizes_instances_from_a_module():
    agents = load_agents("examples.knowledge.agents")
    assert agents
    assert all(hasattr(a, "consumes") for a in agents)


def test_load_agents_rejects_unknown_module():
    with pytest.raises(SystemExit):
        load_agents("no.such.module")


def test_graph_command_prints_mermaid(capsys):
    assert main(["graph", "examples.knowledge.agents"]) == 0
    out = capsys.readouterr().out
    assert "graph" in out or "-->" in out


def _seed_session(path: str) -> str:
    ctx = Context()
    ctx.create(Doc(text="hello"))
    store = SessionStore(open_store(path, "auto"))
    run(store.save_session("s1", ctx))
    return "s1"


def test_context_command_happy_path(tmp_path, capsys):
    db = str(tmp_path / "sessions.sqlite3")
    _seed_session(db)
    assert main(["context", db]) == 0
    assert "s1" not in capsys.readouterr().out  # mermaid, not the raw session id


def test_context_command_missing_store_reports_error(tmp_path, capsys):
    # a parent directory that does not exist makes sqlite3.connect() raise
    # OperationalError, the path `common.open_store` cannot paper over.
    missing = str(tmp_path / "noexist" / "missing.sqlite3")
    assert main(["context", missing]) == 1
    assert "no session store" in capsys.readouterr().out


def test_replay_command_happy_path(tmp_path, capsys):
    db = str(tmp_path / "sessions.sqlite3")
    _seed_session(db)
    assert main(["replay", db]) == 0
    out = capsys.readouterr().out
    assert "replay v" in out
    assert "artifacts: 1" in out


def test_replay_command_with_diagram_flag(tmp_path):
    db = str(tmp_path / "sessions.sqlite3")
    _seed_session(db)
    assert main(["replay", db, "--diagram"]) == 0


def test_replay_command_missing_session_reports_error(tmp_path):
    db = str(tmp_path / "sessions.sqlite3")
    _seed_session(db)
    assert main(["replay", db, "--session", "nope"]) == 1


def test_branch_command_save_then_list(tmp_path, capsys):
    db = str(tmp_path / "sessions.sqlite3")
    _seed_session(db)
    assert main(["branch", db, "s1", "save", "feature-x"]) == 0
    assert "saved branch" in capsys.readouterr().out
    assert main(["branch", db, "s1", "list"]) == 0
    assert "feature-x" in capsys.readouterr().out


def test_branch_command_merge_missing_branch_reports_error(tmp_path, capsys):
    db = str(tmp_path / "sessions.sqlite3")
    _seed_session(db)
    assert main(["branch", db, "s1", "merge", "--into", "a", "--source", "b"]) == 1
    assert "missing branch" in capsys.readouterr().out


def test_trace_command_happy_path(tmp_path, capsys):
    db_path = str(tmp_path / "traces.db")
    store = TraceStore(db_path)
    run(store.export(RunTrace(id="r1", session_id="s", outcome="completed")))
    store.close()
    assert main(["trace", db_path]) == 0
    out = capsys.readouterr().out
    assert "sequenceDiagram" in out


def test_trace_command_empty_store_reports_no_traces(tmp_path, capsys):
    # TraceStore(path) always creates the schema, so this hits "no traces
    # found" rather than the sqlite3.OperationalError branch — still the
    # error path a fresh `reactifact trace some.db` actually takes.
    missing = str(tmp_path / "missing.db")
    assert main(["trace", missing]) == 1
    assert "no traces found" in capsys.readouterr().out
