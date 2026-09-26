"""Opt-in pytest integration for `reactifact.testing`.

Deliberately **not** a `pytest11` entry point: installing `reactifact` must not
change a project's pytest behaviour unless asked. Enable it explicitly:

    # tests/conftest.py
    pytest_plugins = ["reactifact.testing.pytest_plugin"]

It does two things:

- runs `async def` scenario tests via `asyncio.run` — no `pytest-asyncio`
  needed — and turns a `ScenarioSkip` into `pytest.skip`, so a scenario that
  opts out (no API key, no recorded fixture) reports SKIP instead of ERROR;
- provides a `scenario_lab` factory fixture that builds a `ScenarioLab` with
  the ambient `$REACTIFACT_SCENARIO_MODE`, the same switch `reactifact
  scenario --mode` sets — so the same test runs live, records, or replays.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

import pytest

from .exceptions import ScenarioSkip
from .lab import ScenarioLab
from .record import Mode, mode_from_env


@pytest.fixture
def scenario_lab() -> Callable[..., ScenarioLab]:
    """A factory: `scenario_lab(agents, **kwargs) -> ScenarioLab`.

    Defaults `mode=` to `$REACTIFACT_SCENARIO_MODE` (live/record/replay), so a
    scenario behaves the same under pytest as under the `reactifact scenario`
    CLI. Any `ScenarioLab` argument can be overridden per call.
    """

    def make(agents: Any, **kwargs: Any) -> ScenarioLab:
        if "mode" not in kwargs:
            mode: Mode = mode_from_env()
            kwargs["mode"] = mode
        return ScenarioLab(agents, **kwargs)

    return make


def pytest_pyfunc_call(pyfuncitem: Any) -> bool | None:
    """Runs `async def` tests with `asyncio.run`; maps `ScenarioSkip` to skip.

    Only coroutine test functions are handled — synchronous tests are left to
    pytest itself, so this never clobbers another async plugin for them.
    """
    func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(func):
        return None
    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in inspect.signature(func).parameters
        if name in pyfuncitem.funcargs
    }
    try:
        asyncio.run(func(**kwargs))
    except ScenarioSkip as exc:
        pytest.skip(str(exc))
    return True
