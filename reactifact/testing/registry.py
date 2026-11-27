"""`@scenario` registration for the `reactifact scenario` CLI.

Scenarios are plain functions (usually `async def`, wrapping one or more
`ScenarioLab.run()` calls) registered with `@scenario(...)` at import time —
mirroring how `reactifact.cli.common.load_agents` resolves a module path to
`Agent` instances, `collect()` resolves a list of module paths to the
`@scenario`-decorated functions found inside them. There is deliberately no
directory-scanning convention (no `scenario_*.py` globbing): a scenario
module is just a module, imported by its dotted path like any other.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

#: A registered scenario: a human-readable name plus the zero-arg callable
#: that runs it (raises on failure — `AssertionFailure`/`AssertionError` for a
#: failed check, `ScenarioSkip` to opt out, anything else counts as an error).
ScenarioFunc = Callable[[], "Awaitable[None] | None"]


@dataclass(frozen=True)
class ScenarioCase:
    name: str
    func: ScenarioFunc


_REGISTRY: list[ScenarioCase] = []


def scenario(name: str | None = None) -> Callable[[ScenarioFunc], ScenarioFunc]:
    """Registers a function as a scenario, importable by `collect()`.

    `name` defaults to the function's `__name__`; give it an explicit,
    descriptive name (e.g. `"repair: estimate prices from the catalog"`) since
    it's what the CLI prints and what `-k`/`--filter` matches against.
    """

    def decorator(func: ScenarioFunc) -> ScenarioFunc:
        _REGISTRY.append(ScenarioCase(name=name or func.__name__, func=func))
        return func

    return decorator


def collect(modules: list[str]) -> list[ScenarioCase]:
    """Imports `modules` (dotted paths) and returns every scenario they
    registered, in encounter order. Clears the registry first, so re-running
    `collect()` in the same process (e.g. from tests of this module) doesn't
    accumulate duplicates from a previous import.

    A plain `import_module` is a no-op for a module already in `sys.modules`
    — its `@scenario` decorators would not re-run against the just-cleared
    registry, silently dropping that module's cases. `_reimport` reloads an
    already-imported module (and, for a package, every already-imported
    submodule — that's where a scenarios package like `examples.repair.
    scenarios` actually keeps its `@scenario` functions) so `collect()` gives
    the same result no matter how many times it's called in one process.
    """
    _REGISTRY.clear()
    for module_name in modules:
        try:
            _reimport(module_name)
        except ModuleNotFoundError as exc:
            raise SystemExit(f"could not import {module_name!r}: {exc}") from exc
    return list(_REGISTRY)


def _reimport(module_name: str) -> None:
    if module_name not in sys.modules:
        importlib.import_module(module_name)
        return
    prefix = f"{module_name}."
    tree = [n for n in sys.modules if n == module_name or n.startswith(prefix)]
    # deepest submodules first, so a package's `from . import child` (run when
    # the package itself reloads) picks up already-fresh children rather than
    # re-triggering their (already-cached) import.
    for name in sorted(tree, key=lambda n: n.count("."), reverse=True):
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)


__all__ = ["ScenarioCase", "collect", "scenario"]
