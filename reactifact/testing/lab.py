"""`ScenarioLab` — the single entry point for `reactifact.testing`.

Ties `fault.py` (tool fault injection + call recording), `mock.py` (fault
injection for any other resource — the LLM, the embedder, a source),
`record.py` (record/replay LLM wrapping) and `assertions.py` (chained
assertions) into one "seed some artifacts, run the agents, assert on what
happened" call:

    lab = ScenarioLab([my_agent], resources=lambda: build_resources())
    lab.fail("search", TimeoutError("boom"), times=1)
    lab.fail_resource("llm", ConnectionError("model unreachable"))
    result = await lab.run(Question(text="..."))
    result.artifacts(Answer).exists()
    result.tools.called("search")
    result.events.contains("Searching")
    result.errors.none()

A fresh `Context`/`Runtime` is built on every `run()` — scenarios never share
state, so a queued fault or the tool-call recorder can't leak from one `run()`
into the next.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from reactifact.agents import Agent
from reactifact.budget import Budget, RunStats
from reactifact.context import Context
from reactifact.resources import RuntimeResources
from reactifact.runtime import Runtime
from reactifact.streaming import ProgressEvent, QueueEvent
from reactifact.tracing.models import RunTrace
from reactifact.tracing.tracer import Tracer

from .assertions import (
    ArtifactAssertions,
    ErrorAssertions,
    EventAssertions,
    LLMAssertions,
    PathAssertions,
    ToolAssertions,
)
from .fault import FaultInstaller, ToolCallRecord, ToolCallRecorder, ToolFault
from .mock import ResourceFault, ResourceFaultInstaller
from .record import Mode, wrap_llm

T = TypeVar("T", bound=BaseModel)


def _drain_events(queue: QueueEvent) -> list[ProgressEvent]:
    """Empties a `context.subscribe()` queue synchronously.

    Safe without an `await`: `Context.announce()` publishes via
    `queue.put_nowait(...)` (never blocks, unbounded queue), and this is only
    ever called after the `runtime.arun()` that produced those events has
    already completed — no concurrent producer to race.
    """
    events: list[ProgressEvent] = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return events


class _CapturingTracer(Tracer):
    """Captures the single `RunTrace` a scenario's one `arun()` produces."""

    def __init__(self) -> None:
        super().__init__()
        self.trace: RunTrace | None = None

    async def on_turn_end(self, trace: RunTrace) -> None:
        self.trace = trace


@dataclass
class ScenarioResult:
    """Everything a scenario assertion needs, read off one `ScenarioLab.run()`.

    `context`/`stats` are exposed directly for anything the assertion groups
    don't cover; the properties below are the intended entry points.
    """

    context: Context
    stats: RunStats | None
    trace: RunTrace | None
    calls: list[ToolCallRecord]
    progress_events: list[ProgressEvent]

    def artifacts(self, artifact_type: type[T]) -> ArtifactAssertions[T]:
        return ArtifactAssertions(self.context, artifact_type)

    @property
    def tools(self) -> ToolAssertions:
        return ToolAssertions(self.calls)

    @property
    def events(self) -> EventAssertions:
        return EventAssertions(self.progress_events)

    @property
    def path(self) -> PathAssertions:
        return PathAssertions(self.trace)

    @property
    def llm(self) -> LLMAssertions:
        return LLMAssertions(self.trace)

    @property
    def errors(self) -> ErrorAssertions:
        return ErrorAssertions(self.trace, self.stats)


class ScenarioLab:
    """Runs `agents` against seeded artifacts, once per `run()` call.

    `mode` controls the LLM behind `resources.llm` (see `reactifact.testing.record`):
    - `"live"` (default): the real provider, unchanged.
    - `"record"`: wraps it in `ReplayLLM(mode="record")`, appending every call
      to `recording_path`.
    - `"replay"`: wraps it in `ReplayLLM(mode="replay")` — no live network
      calls; a call that diverges from the recording raises `ReplayMiss`
      rather than being answered with a guess (§59).

    `isolate_errors` defaults to `True` here (unlike `Runtime`'s own default)
    so a scenario can assert on a crashed agent via `result.errors` instead of
    the whole `run()` raising; pass `False` to let an unexpected agent
    exception fail the test the ordinary way.
    """

    def __init__(
        self,
        agents: list[Agent],
        *,
        resources: RuntimeResources | Callable[[], RuntimeResources] | None = None,
        budget: Budget | None = None,
        max_concurrency: int | None = None,
        isolate_errors: bool = True,
        mode: Mode = "live",
        recording_path: str | Path = "scenario_calls.jsonl",
    ) -> None:
        self._agents = agents
        self._resources = resources
        self._budget = budget
        self._max_concurrency = max_concurrency
        self._isolate_errors = isolate_errors
        self._mode: Mode = mode
        self._recording_path = Path(recording_path)
        self._faults: list[ToolFault] = []
        self._resource_faults: list[ResourceFault] = []

    def fail(
        self,
        tool_name: str,
        error: BaseException | Callable[[], BaseException],
        *,
        times: int | None = None,
    ) -> None:
        """Queues a fault for the next `run()`.

        `tool_name` raises `error` (or the result of calling it, if callable —
        useful for a fresh exception instance per call) instead of executing.
        `times=None` (default) faults every call; `times=N` faults the first
        `N` calls, then delegates to the real tool. Queued faults are consumed
        by the next `run()` and don't carry over to the one after it.
        """
        self._faults.append(ToolFault(tool_name, error, times=times))

    def fail_resource(
        self,
        name: str,
        error: BaseException | Callable[[], BaseException],
        *,
        method: str | None = None,
        times: int | None = None,
    ) -> None:
        """Queues a fault for a named resource — the general-purpose analog
        of `fail()` for anything that isn't a tool: `"llm"`, `"embedder"`, a
        source id (`resources.sources[id]`), or a name set via
        `resources.set(name, ...)`.

        Wraps the resource in a duck-typed proxy for the next `run()`/
        `.turn()`: `method=None` (default) fails every callable on it;
        naming one method (e.g. `"embed"`, `"search"`) faults only that
        method. `times=None` faults every call; `times=N` faults the first
        `N`, then delegates to the real resource — same shape as `fail()`.
        Raises `ScenarioError` at run time if `name` doesn't match any
        resource, or matches one that's `None` (nothing configured to fail).
        """
        self._resource_faults.append(
            ResourceFault(name, error, method=method, times=times)
        )

    def _build_resources(self) -> RuntimeResources:
        resources = self._resources() if callable(self._resources) else self._resources
        resources = resources or RuntimeResources()
        resources.llm = wrap_llm(
            resources.llm, mode=self._mode, recording_path=self._recording_path
        )
        return resources

    async def run(self, *seed: Any, max_iterations: int = 100) -> ScenarioResult:
        """Seeds `seed` artifacts into a fresh `Context`, runs the agents to
        completion (or budget/iteration exhaustion), and returns the result.
        """
        context = Context(resources=self._build_resources())
        for data in seed:
            context.create(data)
        return await _execute_turn(
            agents=self._agents,
            faults=self._take_faults(),
            resource_faults=self._take_resource_faults(),
            budget=self._budget,
            max_concurrency=self._max_concurrency,
            isolate_errors=self._isolate_errors,
            context=context,
            max_iterations=max_iterations,
        )

    def scenario(self) -> Scenario:
        """Starts a multi-turn scenario: one `Context` reused across `.turn()`
        calls, for flows that need more than one round of user input to reach
        the state under test (e.g. a design pick, then a plan, then an
        approval — each its own turn on the same project). The record/replay
        LLM wrapper is built once here, so its call index persists correctly
        across turns.
        """
        context = Context(resources=self._build_resources())
        return Scenario(
            agents=self._agents,
            budget=self._budget,
            max_concurrency=self._max_concurrency,
            isolate_errors=self._isolate_errors,
            take_faults=self._take_faults,
            take_resource_faults=self._take_resource_faults,
            context=context,
        )

    def _take_faults(self) -> list[ToolFault]:
        """Pops and clears the queued tool faults — one-shot, for the next turn."""
        faults, self._faults = self._faults, []
        return faults

    def _take_resource_faults(self) -> list[ResourceFault]:
        """Pops and clears the queued resource faults — one-shot, for the
        next turn."""
        faults, self._resource_faults = self._resource_faults, []
        return faults


async def _execute_turn(
    *,
    agents: Sequence[Agent],
    faults: list[ToolFault],
    resource_faults: list[ResourceFault],
    budget: Budget | None,
    max_concurrency: int | None,
    isolate_errors: bool,
    context: Context,
    max_iterations: int,
) -> ScenarioResult:
    """Runs one turn to completion: install faults/recorder, `runtime.arun()`,
    restore the tools/resources, return the result. Shared by
    `ScenarioLab.run()` and `Scenario.turn()` so there is exactly one place
    that builds a `Runtime`.
    """
    recorder = ToolCallRecorder()
    tracer = _CapturingTracer()
    runtime = Runtime(
        context,
        agents=list(agents),
        budget=budget,
        max_concurrency=max_concurrency,
        tracer=tracer,
        isolate_errors=isolate_errors,
    )
    progress_queue = context.subscribe()
    try:
        with (
            FaultInstaller(agents, faults, recorder, resources=context.resources),
            ResourceFaultInstaller(context.resources, resource_faults),
        ):
            await runtime.arun(max_iterations=max_iterations)
    finally:
        progress_events = _drain_events(progress_queue)
        context.unsubscribe(progress_queue)

    return ScenarioResult(
        context=context,
        stats=runtime.last_stats,
        trace=tracer.trace,
        calls=recorder.calls,
        progress_events=progress_events,
    )


def _combined_trace(traces: list[RunTrace]) -> RunTrace | None:
    if not traces:
        return None
    return RunTrace(spans=[span for trace in traces for span in trace.spans])


class Scenario:
    """A multi-turn scenario: one `Context`/agent set reused across `.turn()`
    calls (see `ScenarioLab.scenario()`).

    Each `.turn()` seeds new artifacts onto the *same* context (so a later
    turn sees everything an earlier one produced), runs the agents to
    completion, and returns that turn's own `ScenarioResult` — faults queued
    via `lab.fail(...)` are still one-shot, consumed by the next `.turn()`
    only. `.path`/`.tools`/`.events`/`.llm`/`.errors` mirror `ScenarioResult`'s,
    but aggregated over every turn run so far, for conversation-wide
    assertions (e.g. "the model was never called across the whole exchange").
    """

    def __init__(
        self,
        *,
        agents: Sequence[Agent],
        budget: Budget | None,
        max_concurrency: int | None,
        isolate_errors: bool,
        take_faults: Callable[[], list[ToolFault]],
        take_resource_faults: Callable[[], list[ResourceFault]],
        context: Context,
    ) -> None:
        self._agents = agents
        self._budget = budget
        self._max_concurrency = max_concurrency
        self._isolate_errors = isolate_errors
        self._take_faults = take_faults
        self._take_resource_faults = take_resource_faults
        self.context = context
        self.all_traces: list[RunTrace] = []
        self.all_calls: list[ToolCallRecord] = []
        self.all_progress_events: list[ProgressEvent] = []

    async def turn(self, *seed: Any, max_iterations: int = 100) -> ScenarioResult:
        for data in seed:
            self.context.create(data)
        result = await _execute_turn(
            agents=self._agents,
            faults=self._take_faults(),
            resource_faults=self._take_resource_faults(),
            budget=self._budget,
            max_concurrency=self._max_concurrency,
            isolate_errors=self._isolate_errors,
            context=self.context,
            max_iterations=max_iterations,
        )
        if result.trace is not None:
            self.all_traces.append(result.trace)
        self.all_calls.extend(result.calls)
        self.all_progress_events.extend(result.progress_events)
        return result

    @property
    def path(self) -> PathAssertions:
        """Agent path across every turn run so far, in order."""
        return PathAssertions(_combined_trace(self.all_traces))

    @property
    def tools(self) -> ToolAssertions:
        """Tool calls across every turn run so far."""
        return ToolAssertions(self.all_calls)

    @property
    def events(self) -> EventAssertions:
        """Progress events across every turn run so far."""
        return EventAssertions(self.all_progress_events)

    @property
    def llm(self) -> LLMAssertions:
        """LLM usage across every turn run so far."""
        return LLMAssertions(_combined_trace(self.all_traces))

    @property
    def errors(self) -> ErrorAssertions:
        """Isolated agent errors across every turn run so far."""
        return ErrorAssertions(_combined_trace(self.all_traces), None)
