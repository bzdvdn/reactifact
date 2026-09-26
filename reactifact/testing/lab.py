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
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from reactifact.agents import Agent
from reactifact.audit import context_hash
from reactifact.budget import Budget, RunStats
from reactifact.context import Context
from reactifact.resources import ResourceKey, RuntimeResources
from reactifact.runtime import Runtime
from reactifact.streaming import ProgressEvent, QueueEvent
from reactifact.tools import ToolOutput
from reactifact.tracing.models import RunTrace
from reactifact.tracing.tracer import Tracer

from .assertions import (
    ArtifactAssertions,
    ErrorAssertions,
    EventAssertions,
    LLMAssertions,
    PathAssertions,
    RelationAssertions,
    ToolAssertions,
)
from .fault import FaultInstaller, ToolCallRecord, ToolCallRecorder, ToolFault
from .golden import GoldenRun, assert_golden, assert_golden_file
from .mock import UNSET, ResourceFault, ResourceFaultInstaller
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
class ScenarioReport:
    """Aggregate cost/shape of a scenario run: what it took, in one object.

    The reporting counterpart of the assertion groups — token totals, LLM/tool
    counts, the agents that ran and the outcome, with `to_dict()` for a CI/log
    artifact and `render()` for a readable multi-line summary. A single
    `ScenarioResult.report` covers one turn; `Scenario.report` aggregates every
    turn run so far.
    """

    turns: int = 0
    spans: int = 0
    agents: list[str] = field(default_factory=list)
    tool_calls: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: float = 0.0
    errors: int = 0
    outcomes: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "spans": self.spans,
            "agents": list(self.agents),
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
            "outcomes": list(self.outcomes),
        }

    def render(self) -> str:
        rows = [
            ("turns", str(self.turns)),
            ("agents", ", ".join(self.agents) or "—"),
            ("spans", str(self.spans)),
            ("tool calls", str(self.tool_calls)),
            ("llm calls", str(self.llm_calls)),
            (
                "tokens",
                f"{self.prompt_tokens} in / {self.completion_tokens} out"
                f" / {self.total_tokens} total",
            ),
            ("duration", f"{self.duration_ms:.1f} ms"),
            ("errors", str(self.errors)),
            ("outcomes", ", ".join(self.outcomes) or "—"),
        ]
        width = max(len(key) for key, _ in rows)
        return "\n".join(
            ["scenario report"] + [f"  {key:<{width}}  {value}" for key, value in rows]
        )


def _scenario_report(
    traces: list[RunTrace],
    calls: list[ToolCallRecord],
    *,
    turns: int | None = None,
    errors: int | None = None,
) -> ScenarioReport:
    """Builds a `ScenarioReport` off one or more run traces + recorded calls."""
    spans = [span for trace in traces for span in trace.spans]
    llm = [call for trace in traces for call in trace.llm_calls]
    outcomes: list[str] = []
    for trace in traces:
        if trace.outcome and trace.outcome not in outcomes:
            outcomes.append(trace.outcome)
    return ScenarioReport(
        turns=len(traces) if turns is None else turns,
        spans=len(spans),
        agents=sorted({span.agent for span in spans}),
        tool_calls=len(calls),
        llm_calls=len(llm),
        prompt_tokens=sum(call.prompt_tokens for call in llm),
        completion_tokens=sum(call.completion_tokens for call in llm),
        duration_ms=round(sum(trace.duration_ms for trace in traces), 1),
        errors=(sum(1 for span in spans if span.error) if errors is None else errors),
        outcomes=outcomes,
    )


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
    def report(self) -> ScenarioReport:
        """Token/latency/tool totals for this one turn (see `ScenarioReport`)."""
        traces = [self.trace] if self.trace is not None else []
        errors = None
        if not traces and self.stats is not None:
            errors = self.stats.errors
        return _scenario_report(traces, self.calls, turns=1, errors=errors)

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

    @property
    def relations(self) -> RelationAssertions:
        return RelationAssertions(self.context)

    @property
    def context_hash(self) -> str:
        """The run's reproducible fingerprint (`reactifact.audit.context_hash`)."""
        return context_hash(self.context)

    def assert_golden(
        self, target: GoldenRun | str | Path, *, update: bool | None = None
    ) -> GoldenRun:
        """Snapshot assertion over this turn — see `golden.assert_golden_file`.

        `target` is a `GoldenRun` or a path. A missing file (or `update=True`/
        `$REACTIFACT_GOLDEN_UPDATE=1`) records the snapshot instead of failing,
        so the first run seeds it and later drift fails.
        """
        if isinstance(target, GoldenRun):
            assert_golden(self.context, target, trace=self.trace)
            return target
        return assert_golden_file(self.context, target, trace=self.trace, update=update)

    def explain(self) -> str:
        """A human-readable dump of everything the run produced.

        The debugging counterpart to the assertion groups: artifacts by type,
        the relation graph, the agent path, tool/LLM calls and errors in one
        string — what you want in a failing test's output, or a
        `print(result.explain())` when a scenario surprises you.
        """
        return _render_explain(
            context=self.context,
            report=self.report,
            path=self.path.all(),
            calls=self.calls,
            trace=self.trace,
        )


_CURRENT_RESULT: ContextVar[ScenarioResult | None] = ContextVar(
    "reactifact_scenario_result", default=None
)


def current_result() -> ScenarioResult | None:
    """The most recent `ScenarioResult` this task produced.

    Set by `ScenarioLab.run()`/`Scenario.turn()`; lets tooling (the
    `reactifact scenario` CLI's failure report) show the run state when a
    *later* assertion in the scenario raises. Scoped to the running task, so
    it never leaks between separate `asyncio.run()` scenarios.
    """
    return _CURRENT_RESULT.get()


def _render_explain(
    *,
    context: Context,
    report: ScenarioReport,
    path: list[str],
    calls: list[ToolCallRecord],
    trace: RunTrace | None,
) -> str:
    """Shared body of `ScenarioResult.explain()`/`Scenario.explain()`."""
    lines: list[str] = [report.render(), "", "artifacts:"]
    by_type: dict[str, list[str]] = {}
    for artifact in context.list_artifacts():
        by_type.setdefault(type(artifact.data).__name__, []).append(
            f"{artifact.id} v{artifact.version}"
        )
    if by_type:
        for name in sorted(by_type):
            lines.append(f"  {name}: {', '.join(by_type[name])}")
    else:
        lines.append("  (none)")

    relations = context.relations()
    lines.append("relations:")
    if relations:
        for rel in relations:
            lines.append(f"  {rel.source_id} --{rel.relation}--> {rel.target_id}")
    else:
        lines.append("  (none)")

    lines.append(f"path: {path}")
    lines.append("tools:")
    if calls:
        for call in calls:
            status = f"error: {call.error}" if call.error else "ok"
            lines.append(f"  {call.tool}({call.args}) -> {status}")
    else:
        lines.append("  (none)")

    llm = trace.llm_calls if trace is not None else []
    lines.append(
        f"llm: {len(llm)} call(s), {sum(c.prompt_tokens for c in llm)} in / "
        f"{sum(c.completion_tokens for c in llm)} out"
    )
    errored = [
        span for span in (trace.spans if trace is not None else []) if span.error
    ]
    lines.append(f"errors: {len(errored)}")
    for span in errored:
        lines.append(f"  {span.agent}: {span.error}")
    return "\n".join(lines)


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
        when: Callable[[dict[str, Any]], bool] | None = None,
        delay: float = 0.0,
    ) -> None:
        """Queues a fault for the next `run()`/`.turn()`.

        `tool_name` raises `error` (or the result of calling it, if callable —
        useful for a fresh exception instance per call) instead of executing.
        `times=None` (default) faults every call; `times=N` faults the first
        `N` calls, then delegates to the real tool; `when(args)` narrows it to
        matching calls; `delay` seconds are slept before raising (a
        slow-then-fail tool). Queued faults are one-shot — consumed by the next
        `run()`/`.turn()` only.
        """
        self._faults.append(
            ToolFault(tool_name, error, times=times, when=when, delay=delay)
        )

    def stub_tool(
        self,
        tool_name: str,
        *,
        text: str = "",
        error: str | None = None,
        times: int | None = None,
        when: Callable[[dict[str, Any]], bool] | None = None,
        delay: float = 0.0,
    ) -> None:
        """Queues a canned-response stub for `tool_name` — the non-failing
        counterpart of `fail()`: the tool returns `ToolOutput(text=..., error=...)`
        instead of executing. Same `times`/`when`/`delay` semantics.
        """
        self._faults.append(
            ToolFault(
                tool_name,
                times=times,
                when=when,
                delay=delay,
                output=ToolOutput(text=text, error=error or ""),
            )
        )

    def fail_resource(
        self,
        resource: str | type[Any] | ResourceKey[Any],
        error: BaseException | Callable[[], BaseException],
        *,
        method: str | None = None,
        times: int | None = None,
        when: Callable[[tuple[Any, ...], dict[str, Any]], bool] | None = None,
        delay: float = 0.0,
    ) -> None:
        """Queues a fault for a resource — the general-purpose analog of
        `fail()` for anything that isn't a tool.

        `resource` addresses it either by **string name** — `"llm"`,
        `"embedder"`, a source id (`resources.sources[id]`), or a name set via
        `resources.set(name, ...)` — or by a **typed key**: a class or a
        `ResourceKey` registered with `resources.register(...)`.

        Wraps the resource in a duck-typed proxy for the next `run()`/
        `.turn()`: `method=None` (default) fails every callable on it;
        naming one method (e.g. `"embed"`, `"search"`) faults only that
        method. `times=None` faults every call; `times=N` faults the first
        `N`, then delegates; `when(args, kwargs)` narrows it to matching calls;
        `delay` seconds are slept first. Raises `ScenarioError` at run time if
        `resource` doesn't match any resource, or matches one that's `None`
        (nothing configured to fail).
        """
        self._resource_faults.append(
            ResourceFault(
                resource, error, method=method, times=times, when=when, delay=delay
            )
        )

    def stub_resource(
        self,
        resource: str | type[Any] | ResourceKey[Any],
        *,
        returns: Any = UNSET,
        side_effect: list[Any] | Callable[..., Any] | None = None,
        method: str | None = None,
        times: int | None = None,
        when: Callable[[tuple[Any, ...], dict[str, Any]], bool] | None = None,
        delay: float = 0.0,
    ) -> None:
        """Queues a stub for a resource — the non-failing counterpart of
        `fail_resource()` for the same addressing (string name or typed key).

        `returns` is returned on every stubbed call; or `side_effect` walks a
        sequence/list (`unittest.mock` semantics: an item that is an
        `Exception` raises, anything else is returned) or a callable called as
        `side_effect(*args, **kwargs)`. `times`/`when`/`delay`/`method` behave
        as on `fail_resource`.
        """
        self._resource_faults.append(
            ResourceFault(
                resource,
                method=method,
                times=times,
                returns=returns,
                side_effect=side_effect,
                when=when,
                delay=delay,
            )
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

    def run_sync(self, *seed: Any, max_iterations: int = 100) -> ScenarioResult:
        """Synchronous `run()` — mirrors `Runtime.run()`/`run_once()` for plain
        (non-async) pytest tests. Uses `asyncio.run`, so it can't be called from
        inside an already-running event loop."""
        return asyncio.run(self.run(*seed, max_iterations=max_iterations))

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

    result = ScenarioResult(
        context=context,
        stats=runtime.last_stats,
        trace=tracer.trace,
        calls=recorder.calls,
        progress_events=progress_events,
    )
    _CURRENT_RESULT.set(result)
    return result


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

    def turn_sync(self, *seed: Any, max_iterations: int = 100) -> ScenarioResult:
        """Synchronous `turn()` — see `ScenarioLab.run_sync`."""
        return asyncio.run(self.turn(*seed, max_iterations=max_iterations))

    @property
    def relations(self) -> RelationAssertions:
        """Relations across every turn run so far (the shared `Context`)."""
        return RelationAssertions(self.context)

    def assert_golden(
        self, target: GoldenRun | str | Path, *, update: bool | None = None
    ) -> GoldenRun:
        """Aggregate snapshot assertion across every turn run so far — see
        `ScenarioResult.assert_golden`."""
        trace = _combined_trace(self.all_traces)
        if isinstance(target, GoldenRun):
            assert_golden(self.context, target, trace=trace)
            return target
        return assert_golden_file(self.context, target, trace=trace, update=update)

    def explain(self) -> str:
        """Aggregate `explain()` across every turn run so far — see
        `ScenarioResult.explain()`."""
        return _render_explain(
            context=self.context,
            report=self.report,
            path=self.path.all(),
            calls=self.all_calls,
            trace=_combined_trace(self.all_traces),
        )

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

    @property
    def report(self) -> ScenarioReport:
        """Aggregate `ScenarioReport` across every turn run so far."""
        return _scenario_report(self.all_traces, self.all_calls)
