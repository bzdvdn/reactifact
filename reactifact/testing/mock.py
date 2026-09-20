"""Generic resource-level fault injection for `reactifact.testing`.

`fault.py` covers tools (the only thing reactifact keeps in a named registry).
Everything else an agent depends on lives on `RuntimeResources` — the LLM,
the embedder, a named `Source`, or an arbitrary object stashed via
`resources.set(name, ...)` — each with a *different* shape (`llm.complete`/
`.stream`, `embedder.embed`, `source.search`/`.asearch`/`.resolve`, or
whatever an app-specific resource exposes). reactifact never does `isinstance`
checks against its own provider/source ABCs (see `fault.py`'s own docstring
— everything is duck-typed), so a single reflection-based proxy that
intercepts calls by name is a safe stand-in for any of them: this is the
one general "mock this resource, make it fail, watch the honest-fallback
path (§59) kick in" primitive, for whatever `resources.llm`/`.embedder`/
`.sources[...]`/`.get(...)` isn't a tool.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .exceptions import ScenarioError

if TYPE_CHECKING:
    from reactifact.resources import ResourceKey, RuntimeResources


@dataclass
class ResourceFault:
    """A queued fault for one resource.

    `resource` addresses it either by **string name** — `"llm"`, `"embedder"`,
    a source id (`resources.sources[id]`), or a name set via
    `resources.set(name, ...)` — or by a **typed key**: a class or a
    `ResourceKey` registered with `resources.register(...)`. `method=None`
    (default) fails every callable on the resource; naming one (e.g.
    `"embed"`) faults only that method, leaving the rest of the resource
    working normally. `times=None` faults every call; `times=N` faults the
    first `N`, then delegates to the real resource — same shape as
    `fault.ToolFault`.
    """

    resource: str | type[Any] | ResourceKey[Any]
    error: BaseException | Callable[[], BaseException]
    method: str | None = None
    times: int | None = None


def _get_resource(
    resources: RuntimeResources, key: str | type[Any] | ResourceKey[Any]
) -> tuple[Any, bool]:
    """Returns `(value, found)` — `found=False` means no such resource exists
    at all (as opposed to existing but being `None`)."""
    if not isinstance(key, str):
        return (resources.get(key), True) if resources.has(key) else (None, False)
    name = key
    if name == "llm":
        return resources.llm, True
    if name == "embedder":
        return resources.embedder, True
    if name in resources.sources:
        return resources.sources[name], True
    if name in resources.additional:
        return resources.additional[name], True
    return None, False


def _set_resource(
    resources: RuntimeResources, key: str | type[Any] | ResourceKey[Any], value: Any
) -> None:
    if not isinstance(key, str):
        resources.register(key, value)
        return
    name = key
    if name == "llm":
        resources.llm = value
    elif name == "embedder":
        resources.embedder = value
    elif name in resources.sources:
        resources.sources[name] = value
    else:
        resources.set(name, value)


class _FailingProxy:
    """Wraps `inner`, intercepting `fault.method` (or every public callable,
    if `method=None`) to raise `fault.error` instead of delegating, for the
    fault's next `times` calls (or forever). Everything else — attributes,
    other methods — passes straight through to `inner`.
    """

    def __init__(self, inner: Any, fault: ResourceFault) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_fault", fault)
        object.__setattr__(self, "_remaining", fault.times)

    def _take_error(self, name: str) -> BaseException | None:
        fault: ResourceFault = object.__getattribute__(self, "_fault")
        if fault.method is not None and name != fault.method:
            return None
        remaining = object.__getattribute__(self, "_remaining")
        if remaining is not None:
            if remaining <= 0:
                return None
            object.__setattr__(self, "_remaining", remaining - 1)
        return fault.error() if callable(fault.error) else fault.error

    def __getattr__(self, name: str) -> Any:
        attr = getattr(object.__getattribute__(self, "_inner"), name)
        if not callable(attr) or name.startswith("_"):
            return attr

        if inspect.isasyncgenfunction(attr):

            async def _failing_agen(*args: Any, **kwargs: Any) -> Any:
                err = self._take_error(name)
                if err is not None:
                    raise err
                async for item in attr(*args, **kwargs):
                    yield item

            return _failing_agen

        if inspect.iscoroutinefunction(attr):

            async def _failing_coro(*args: Any, **kwargs: Any) -> Any:
                err = self._take_error(name)
                if err is not None:
                    raise err
                return await attr(*args, **kwargs)

            return _failing_coro

        def _failing_sync(*args: Any, **kwargs: Any) -> Any:
            err = self._take_error(name)
            if err is not None:
                raise err
            return attr(*args, **kwargs)

        return _failing_sync


class ResourceFaultInstaller:
    """Context manager: wraps the resources named in `faults` for the
    duration of one turn, restoring the originals in `__exit__` even if the
    wrapped run raises.
    """

    def __init__(
        self, resources: RuntimeResources, faults: list[ResourceFault]
    ) -> None:
        self._resources = resources
        self._faults = {f.resource: f for f in faults}
        self._originals: list[tuple[str | type[Any] | ResourceKey[Any], Any]] = []

    def __enter__(self) -> ResourceFaultInstaller:
        for name, fault in self._faults.items():
            original, found = _get_resource(self._resources, name)
            if not found:
                raise ScenarioError(
                    f"fail_resource({name!r}, ...): no such resource — expected "
                    '"llm", "embedder", a source id, a name set via '
                    "resources.set(...), or a key registered via "
                    "resources.register(Type | ResourceKey, ...)"
                )
            if original is None:
                raise ScenarioError(
                    f"fail_resource({name!r}, ...): resource is None (not "
                    "configured for this scenario) — nothing to fail"
                )
            self._originals.append((name, original))
            _set_resource(self._resources, name, _FailingProxy(original, fault))
        return self

    def __exit__(self, *exc: object) -> None:
        for name, original in self._originals:
            _set_resource(self._resources, name, original)
        self._originals.clear()
