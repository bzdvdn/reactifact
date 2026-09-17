"""Tool-level fault injection and call recording for `reactifact.testing`.

reactifact has no central tool registry. Tools show up in one of two shapes,
and this module discovers and wraps both in place (restoring the originals
afterward):

- **Static** — a plain `dict[str, Tool]` on whichever `ToolUse`/`ToolUseHITL`
  produce instance an agent wires up (`reactifact/tool_use.py`), fixed at
  construction time. `_iter_tool_dicts` finds these by scanning every
  agent's `produces`.
- **Dynamic** — a `list[Tool]` resolved at produce-time from
  `RuntimeResources` (e.g. `context.resources.get("my_tools")`), for a
  produce that builds its own tool-calling loop instead of using `ToolUse`
  (native tool-calling via `reactifact.native_tool_use` is the common case).
  There is no static attribute for these — the list only exists once
  `resources` is built — so `_iter_tool_lists` finds them by scanning
  `RuntimeResources.additional` instead, for any list whose items all look
  like tools (duck-typed on `.execute`, same as `_iter_tool_dicts`).

Both containers are mutated in place (`tools[name] = wrapped` /
`tools_list[index] = wrapped`) since `dict.__setitem__`/`list.__setitem__`
share the same syntax — `FaultInstaller` restores by key either way.

**Important caveat**: `_ToolLoopBase._run_tool` (`reactifact/tool_use.py`)
catches any exception raised by `Tool.execute` and turns it into a plain text
string handed back to the LLM (`"Tool 'x' failed: ..."`) — it never
propagates out of the produce. So a fault injected via `lab.fail(...)` does
**not** abort the scenario run: the agent's LLM sees a tool-failure message
and may retry, give up, or answer anyway, exactly like a real transient tool
failure. `result.tools.called(name)` will show the failed call (`.error` set)
while `result.errors.none()` can still legitimately pass — the agent didn't
crash, it just saw an error and continued. Tools invoked directly from custom
`Produce` code (outside a `ToolUse` loop) are not affected by this
swallowing: an injected exception propagates normally there.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from reactifact.tools import Tool, ToolOutput

if TYPE_CHECKING:
    from reactifact.agents import Agent
    from reactifact.resources import RuntimeResources


@dataclass
class ToolFault:
    """A queued fault for one tool name.

    `times=None` (default) raises on every call; `times=N` raises for the
    first `N` calls, then delegates to the real tool — the natural shape for
    testing "fails then recovers on retry" behavior.
    """

    tool_name: str
    error: BaseException | Callable[[], BaseException]
    times: int | None = None


@dataclass
class ToolCallRecord:
    """One recorded tool invocation, real or faulted."""

    tool: str
    args: dict[str, Any]
    output: ToolOutput | None
    error: str | None


class ToolCallRecorder:
    """Collects `ToolCallRecord`s. Installed on every run, fault or not."""

    def __init__(self) -> None:
        self.calls: list[ToolCallRecord] = []

    def record(
        self,
        tool: str,
        args: dict[str, Any],
        output: ToolOutput | None,
        error: str | None,
    ) -> None:
        self.calls.append(
            ToolCallRecord(tool=tool, args=args, output=output, error=error)
        )


def _iter_tool_dicts(agents: Sequence[Agent]) -> Iterator[dict[str, Tool]]:
    """Yields every `dict[str, Tool]`-shaped mapping found on any agent produce.

    Duck-typed (checks `.execute` on the values) rather than importing the
    private `_ToolLoopBase` class, so it keeps working if that internal is
    renamed or restructured.
    """
    for agent in agents:
        for produce_obj in getattr(agent, "produces", None) or []:
            tools = getattr(produce_obj, "tools", None)
            if (
                isinstance(tools, dict)
                and tools
                and all(hasattr(v, "execute") for v in tools.values())
            ):
                yield tools


def _iter_tool_lists(resources: RuntimeResources | None) -> Iterator[list[Tool]]:
    """Yields every `list[Tool]`-shaped value found in `resources.additional`.

    Covers tools resolved dynamically at produce-time (`context.resources.get(...)`)
    rather than fixed on a `Produce` instance at construction — `_iter_tool_dicts`
    has nothing to scan for those, since no static attribute holds them. Same
    duck-typing as `_iter_tool_dicts`: any non-empty list whose items all have
    `.execute` is treated as a tool list, whatever key it's stored under.
    """
    if resources is None:
        return
    for value in resources.additional.values():
        if isinstance(value, list) and value and all(hasattr(t, "execute") for t in value):
            yield value


class _WrappedTool(Tool):
    """Wraps one `Tool` instance: records every call, and raises `fault`'s
    error for its first `fault.times` calls (or forever, if `times=None`)."""

    def __init__(
        self, inner: Tool, fault: ToolFault | None, recorder: ToolCallRecorder
    ) -> None:
        self.name = inner.name
        self.description = inner.description
        self.destructive = inner.destructive
        self.schema = inner.schema
        self._inner = inner
        self._fault = fault
        self._remaining = fault.times if fault is not None else None
        self._recorder = recorder

    async def execute(self, args: dict[str, Any]) -> ToolOutput:
        fault = self._fault
        if fault is not None and (self._remaining is None or self._remaining > 0):
            if self._remaining is not None:
                self._remaining -= 1
            err = fault.error() if callable(fault.error) else fault.error
            self._recorder.record(self.name, args, None, str(err))
            raise err
        try:
            output = await self._inner.execute(args)
        except Exception as exc:
            self._recorder.record(self.name, args, None, str(exc))
            raise
        self._recorder.record(self.name, args, output, output.error or None)
        return output


def _wrap(tool: Tool, fault: ToolFault | None, recorder: ToolCallRecorder) -> Tool:
    return _WrappedTool(tool, fault, recorder)


class FaultInstaller:
    """Context manager: wraps matching tools for the duration of one run.

    Restores the exact original `Tool` objects in `__exit__`, even if the
    wrapped run raises. `resources` is optional and only needed to reach
    dynamically-resolved tool lists (`_iter_tool_lists`) — omit it (or pass
    `None`) if every tool in play lives in a static `Produce.tools` dict.
    """

    def __init__(
        self,
        agents: Sequence[Agent],
        faults: list[ToolFault],
        recorder: ToolCallRecorder,
        resources: RuntimeResources | None = None,
    ) -> None:
        self._agents = agents
        self._faults = {f.tool_name: f for f in faults}
        self._recorder = recorder
        self._resources = resources
        self._originals: list[tuple[dict[str, Tool] | list[Tool], str | int, Tool]] = []

    def __enter__(self) -> FaultInstaller:
        seen: set[int] = set()
        for tools in _iter_tool_dicts(self._agents):
            if id(tools) in seen:
                continue
            seen.add(id(tools))
            for name, tool in list(tools.items()):
                self._originals.append((tools, name, tool))
                tools[name] = _wrap(tool, self._faults.get(name), self._recorder)
        for tools_list in _iter_tool_lists(self._resources):
            if id(tools_list) in seen:
                continue
            seen.add(id(tools_list))
            for index, tool in enumerate(list(tools_list)):
                self._originals.append((tools_list, index, tool))
                tools_list[index] = _wrap(tool, self._faults.get(tool.name), self._recorder)
        return self

    def __exit__(self, *exc: object) -> None:
        for container, key, original in self._originals:
            container[key] = original  # type: ignore[index]
        self._originals.clear()
