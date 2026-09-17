"""Assertion objects for `reactifact.testing` scenario results.

Each assertion group is a thin, chainable wrapper over data reactifact already
computes — `Context.list_artifacts()` for artifacts, `RunTrace` for the agent
path and LLM calls, (for tools) the harness's own `ToolCallRecorder`
(`reactifact.testing.fault`), since `AgentSpan` has no field for raw tool
invocations, and (for progress/status events) the harness's own subscription
to `context.announce()` for the run's duration. Every failure raises
`AssertionFailure` with the actually observed data inlined, so a failing
scenario test is debuggable straight from the pytest output.

`EventAssertions` closes what v1 dropped for lack of a faithful reactifact
analog — a LangGraph-style "node status" assertion. `ProgressEvent` used to be
a live-streaming-only concept (`Runtime.astream()`), with nothing capturing it
in a post-hoc `ScenarioResult`; `ScenarioLab`/`Scenario` now subscribe to
`context.announce()` for the duration of each run and hand the captured
events to `result.events`, so this works without the caller wiring up its own
`astream()` consumer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from .exceptions import AssertionFailure

if TYPE_CHECKING:
    from reactifact.budget import RunStats
    from reactifact.context import Context
    from reactifact.streaming import ProgressEvent
    from reactifact.tracing.models import AgentSpan, LLMCall, RunTrace

    from .fault import ToolCallRecord

T = TypeVar("T", bound=BaseModel)


class ArtifactAssertions(Generic[T]):
    """Assertions over `context.list_artifacts(artifact_type)`."""

    def __init__(self, context: Context, artifact_type: type[T]) -> None:
        self._context = context
        self._type = artifact_type

    def all(self) -> list[T]:
        return [a.data for a in self._context.list_artifacts(self._type)]

    def exists(self) -> T:
        """Asserts at least one exists; returns the most recently created."""
        artifact = self._context.latest(self._type)
        if artifact is None:
            raise AssertionFailure(
                f"expected an artifact of type {self._type.__name__!r}, found none "
                f"(context has: {self._present_types()})"
            )
        return artifact.data

    def none(self) -> None:
        found = self.all()
        if found:
            raise AssertionFailure(
                f"expected no {self._type.__name__!r} artifacts, found {len(found)}: "
                f"{found!r}"
            )

    def count(self, n: int) -> None:
        found = self.all()
        if len(found) != n:
            raise AssertionFailure(
                f"expected {n} artifact(s) of type {self._type.__name__!r}, "
                f"found {len(found)}: {found!r}"
            )

    def latest(self) -> T:
        return self.exists()

    def matches(self, pattern: str, *, field: str = "text") -> T:
        """Regex-searches `field` on the latest artifact (default: `.text`)."""
        data = self.exists()
        value = str(getattr(data, field, None))
        if re.search(pattern, value) is None:
            raise AssertionFailure(
                f"{self._type.__name__}.{field} = {value!r} does not match "
                f"pattern {pattern!r}"
            )
        return data

    def field_equals(self, field: str, value: Any) -> T:
        data = self.exists()
        actual = getattr(data, field, None)
        if actual != value:
            raise AssertionFailure(
                f"{self._type.__name__}.{field} = {actual!r}, expected {value!r}"
            )
        return data

    def equals(self, **fields: Any) -> T:
        """Asserts every given field on the latest artifact matches at once."""
        data = self.exists()
        mismatches = {
            name: (getattr(data, name, None), expected)
            for name, expected in fields.items()
            if getattr(data, name, None) != expected
        }
        if mismatches:
            raise AssertionFailure(
                f"{self._type.__name__} field mismatch(es): "
                + ", ".join(
                    f"{name}={actual!r} (expected {expected!r})"
                    for name, (actual, expected) in mismatches.items()
                )
            )
        return data

    def contains(self, substring: str, *, field: str = "text") -> T:
        """Plain substring check on `field` of the latest artifact (no regex)."""
        data = self.exists()
        value = str(getattr(data, field, None))
        if substring not in value:
            raise AssertionFailure(
                f"{self._type.__name__}.{field} = {value!r} does not contain "
                f"{substring!r}"
            )
        return data

    def field_in(self, field: str, values: Iterable[Any]) -> T:
        """Asserts `field` on the latest artifact is one of `values`."""
        data = self.exists()
        options = list(values)
        actual = getattr(data, field, None)
        if actual not in options:
            raise AssertionFailure(
                f"{self._type.__name__}.{field} = {actual!r}, expected one of {options!r}"
            )
        return data

    def _present_types(self) -> str:
        names = sorted({type(a.data).__name__ for a in self._context.list_artifacts()})
        return ", ".join(names) if names else "(none)"


class ToolAssertions:
    """Assertions over recorded tool calls (`ToolCallRecorder`, see `fault.py`).

    Independent of `RunTrace`: `AgentSpan` does not carry raw tool
    invocations, so this reads the harness's own call log instead.
    """

    def __init__(self, calls: list[ToolCallRecord]) -> None:
        self._calls = calls

    def call_order(self) -> list[str]:
        return [c.tool for c in self._calls]

    def called(self, name: str) -> list[ToolCallRecord]:
        matches = [c for c in self._calls if c.tool == name]
        if not matches:
            raise AssertionFailure(
                f"expected tool {name!r} to be called, but it never was "
                f"(called: {self.call_order()})"
            )
        return matches

    def never_called(self, name: str) -> None:
        matches = [c for c in self._calls if c.tool == name]
        if matches:
            raise AssertionFailure(
                f"expected tool {name!r} to never be called, but it was called "
                f"{len(matches)} time(s) with args {[m.args for m in matches]}"
            )

    def called_times(self, name: str, n: int) -> None:
        matches = [c for c in self._calls if c.tool == name]
        if len(matches) != n:
            raise AssertionFailure(
                f"expected tool {name!r} to be called {n} time(s), "
                f"was called {len(matches)} time(s)"
            )

    def called_any(self, *names: str) -> ToolCallRecord:
        """Asserts at least one of `names` was called; returns its first call."""
        for call in self._calls:
            if call.tool in names:
                return call
        raise AssertionFailure(
            f"expected at least one of {list(names)} to be called "
            f"(called: {self.call_order()})"
        )

    def called_with(self, name: str, **kwargs: Any) -> ToolCallRecord:
        for call in self._calls:
            if call.tool != name:
                continue
            if all(call.args.get(k) == v for k, v in kwargs.items()):
                return call
        raise AssertionFailure(
            f"no call to tool {name!r} matched args {kwargs!r} "
            f"(calls: {[(c.tool, c.args) for c in self._calls if c.tool == name]})"
        )


class PathAssertions:
    """Assertions over the agent execution path (`RunTrace.spans[*].agent`)."""

    def __init__(self, trace: RunTrace | None) -> None:
        self._trace = trace

    def all(self) -> list[str]:
        if self._trace is None:
            return []
        return [span.agent for span in self._trace.spans]

    def contains(self, agent_name: str) -> None:
        if agent_name not in self.all():
            raise AssertionFailure(
                f"expected agent {agent_name!r} to have run, path was {self.all()}"
            )

    def not_contains(self, agent_name: str) -> None:
        if agent_name in self.all():
            raise AssertionFailure(
                f"expected agent {agent_name!r} to not run, but path was {self.all()}"
            )

    def times(self, agent_name: str) -> int:
        """How many times `agent_name` appears in the path (a measurement,
        not an assertion — pair it with your own `==` check)."""
        return self.all().count(agent_name)

    def sequence(self, *names: str) -> None:
        """Asserts `names` appear, in order, as a (not-necessarily-contiguous) subsequence."""
        path = self.all()
        pos = 0
        for name in names:
            try:
                pos = path.index(name, pos) + 1
            except ValueError as exc:
                raise AssertionFailure(
                    f"expected subsequence {list(names)} in path {path}, "
                    f"but {name!r} was not found after position {pos}"
                ) from exc

    def exact_sequence(self, *names: str) -> None:
        path = self.all()
        if path != list(names):
            raise AssertionFailure(f"expected path {list(names)}, got {path}")

    def any_of(self, *names: str) -> None:
        """Asserts at least one of `names` ran (membership over the path)."""
        path = self.all()
        if not any(name in path for name in names):
            raise AssertionFailure(
                f"expected at least one of {list(names)} in path, got {path}"
            )


class LLMAssertions:
    """Assertions over `RunTrace.llm_calls` (populated automatically since
    `ScenarioLab` always attaches a tracer)."""

    def __init__(self, trace: RunTrace | None) -> None:
        self._trace = trace

    def _calls(self) -> list[LLMCall]:
        return self._trace.llm_calls if self._trace is not None else []

    @property
    def calls(self) -> int:
        return len(self._calls())

    @property
    def tokens(self) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self._calls())

    def max_calls(self, n: int) -> None:
        if self.calls > n:
            raise AssertionFailure(
                f"expected at most {n} LLM call(s), got {self.calls}"
            )

    def max_tokens(self, n: int) -> None:
        if self.tokens > n:
            raise AssertionFailure(f"expected at most {n} token(s), got {self.tokens}")

    def by_agent(self, agent_name: str) -> list[LLMCall]:
        return [c for c in self._calls() if c.agent == agent_name]


class ErrorAssertions:
    """Assertions over isolated agent errors (`Runtime(isolate_errors=True)`).

    Reads `AgentSpan.error` (per-agent) and `RunStats.errors` (count) — both
    require the scenario's `Runtime` to have been constructed with
    `isolate_errors=True`; otherwise an agent exception propagates before a
    trace/stats exist at all.
    """

    def __init__(self, trace: RunTrace | None, stats: RunStats | None) -> None:
        self._trace = trace
        self._stats = stats

    def _errored_spans(self) -> list[AgentSpan]:
        if self._trace is None:
            return []
        return [span for span in self._trace.spans if span.error]

    def none(self) -> None:
        errored = self._errored_spans()
        if errored:
            raise AssertionFailure(
                f"expected no agent errors, found: "
                f"{[(s.agent, s.error) for s in errored]}"
            )

    def count(self) -> int:
        if self._stats is not None:
            return self._stats.errors
        return len(self._errored_spans())

    def expected(self, agent_name: str) -> AgentSpan:
        for span in self._errored_spans():
            if span.agent == agent_name:
                return span
        raise AssertionFailure(
            f"expected agent {agent_name!r} to have errored, but no error span "
            f"for it was found (errored agents: {[s.agent for s in self._errored_spans()]})"
        )


class EventAssertions:
    """Assertions over `context.announce()` progress events captured while
    the scenario ran (`ProgressEvent.kind`/`.message` — `kind` is the app's
    own category, e.g. `"status"` for domain-facing progress text, `"agent"`
    for framework-internal producers like `ToolUse`; see `Context.announce`'s
    own docstring). `kind=None` (the default on every method below) matches
    any kind.
    """

    def __init__(self, events: list[ProgressEvent]) -> None:
        self._events = events

    def all(self) -> list[ProgressEvent]:
        return list(self._events)

    def messages(self, *, kind: str | None = None) -> list[str]:
        return [e.message for e in self._events if kind is None or e.kind == kind]

    def contains(self, pattern: str, *, kind: str | None = None) -> None:
        if not any(re.search(pattern, m) for m in self.messages(kind=kind)):
            raise AssertionFailure(
                f"expected a {kind or 'any'}-kind progress message matching "
                f"{pattern!r}, got: {self.messages(kind=kind)}"
            )

    def not_contains(self, pattern: str, *, kind: str | None = None) -> None:
        matches = [m for m in self.messages(kind=kind) if re.search(pattern, m)]
        if matches:
            raise AssertionFailure(
                f"expected no {kind or 'any'}-kind progress message matching "
                f"{pattern!r}, found: {matches}"
            )

    def count(self, *, kind: str | None = None) -> int:
        return len(self.messages(kind=kind))

    def min_count(self, n: int, *, kind: str | None = None) -> None:
        actual = self.count(kind=kind)
        if actual < n:
            raise AssertionFailure(
                f"expected at least {n} {kind or 'any'}-kind progress event(s), "
                f"got {actual}: {self.messages(kind=kind)}"
            )
