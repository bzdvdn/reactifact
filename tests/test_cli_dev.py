"""`reactifact dev` — target resolution and uvicorn wiring."""

from __future__ import annotations

import sys

import pytest
import uvicorn
from reactifact.cli import main
from reactifact.cli.dev import _resolve


@pytest.fixture
def dev_module(tmp_path, monkeypatch):
    name = "dev_cli_fixture"
    (tmp_path / f"{name}.py").write_text(
        "def create_app():\n    return object()\n\napp = object()\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)
    yield name
    sys.modules.pop(name, None)


def test_resolve_detects_a_factory_and_an_app(dev_module):
    assert _resolve(f"{dev_module}:create_app") == (f"{dev_module}:create_app", True)
    assert _resolve(f"{dev_module}:app") == (f"{dev_module}:app", False)


def test_resolve_defaults_to_create_app(dev_module):
    assert _resolve(dev_module) == (f"{dev_module}:create_app", True)


def test_dev_passes_the_target_to_uvicorn(dev_module, monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(target, **kwargs):
        seen["target"] = target
        seen.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    code = main(["dev", f"{dev_module}:create_app", "--no-reload", "--port", "0"])

    assert code == 0
    assert seen["target"] == f"{dev_module}:create_app"
    assert seen["factory"] is True
    assert seen["reload"] is False
    assert seen["port"] == 0


def test_dev_factory_flag_overrides_detection(dev_module, monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda target, **kwargs: seen.update(kwargs))

    assert main(["dev", f"{dev_module}:app", "--no-factory", "--no-reload"]) == 0
    assert seen["factory"] is False

    assert main(["dev", f"{dev_module}:app", "--factory", "--no-reload"]) == 0
    assert seen["factory"] is True
