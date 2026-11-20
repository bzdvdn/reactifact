"""Internal: collect `RunEvent`s for `Runtime.astream_events` (§54)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from ..commit import Commit
from ..context import Context
from ..streaming import (
    AgentEnd,
    AgentRetry,
    AgentStart,
    ArtifactWrite,
    GenerationStart,
    LLMDelta,
    RunStart,
    ToolEnd,
    ToolStart,
    Values,
)
from ..tracing import Tracer
from ..tracing.models import AgentSpan


class RunEventCollector(Tracer):
    """A `Tracer` that turns lifecycle hooks into typed `RunEvent`s.

    It is installed only for the duration of `Runtime.astream_events`, on top
    of the user's own tracer (via `CompositeTracer`), and pushes into a bounded
    queue. Lifecycle events are best-effort: if a slow consumer fills the queue,
    the oldest event is dropped rather than blocking the run.
    """

    def __init__(
        self,
        queue: asyncio.Queue[Any],
        *,
        context: Context,
        modes: frozenset[str],
        session_id: str,
        scope: str,
        metadata: dict[str, str],
    ) -> None:
        super().__init__()
        self.queue = queue
        self.context = context
        self.modes = modes
        self.session_id = session_id
        self.scope = scope
        self.metadata = metadata
        self.run_id = ""
        self._last_version = context.version
        self._closed = False

    @property
    def wants_llm_deltas(self) -> bool:
        return "messages" in self.modes

    def close(self) -> None:
        self._closed = True

    def _put(self, event: Any) -> None:
        if self._closed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # A live UI must not back-pressure the runtime: drop the oldest.
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(event)

    # ---- lifecycle ------------------------------------------------- #

    def on_turn_begin(self, run_id: str, *, session_id: str, started_at: Any) -> None:
        self.run_id = run_id
        self._put(
            RunStart(
                run_id=run_id,
                session_id=session_id or self.session_id,
                scope=self.scope,
                metadata=self.metadata,
                started_at=started_at,
            )
        )

    def on_generation_start(self, run_id: str, generation: int) -> None:
        if "debug" in self.modes:
            self._put(GenerationStart(run_id=run_id, generation=generation))

    def on_agent_start(self, agent: Any, event: Any, reads: list[Any]) -> None:
        if "debug" not in self.modes:
            return
        self._put(
            AgentStart(
                run_id=self.run_id,
                agent=agent.name,
                event_type=getattr(event.type, "value", str(event.type)),
                event_artifact_id=event.artifact_id,
                reads=[
                    {"artifact_id": read.artifact_id, "version": read.version}
                    for read in reads
                ],
            )
        )

    def on_agent_retry(self, agent: Any, attempt: int, error: BaseException) -> None:
        if "debug" in self.modes:
            self._put(
                AgentRetry(
                    run_id=self.run_id,
                    agent=agent.name,
                    attempt=attempt,
                    error=f"{type(error).__name__}: {error}",
                )
            )

    def on_span(self, span: AgentSpan) -> None:
        if "debug" not in self.modes:
            return
        self._put(
            AgentEnd(
                run_id=self.run_id,
                agent=span.agent,
                latency_ms=span.latency_ms,
                error=span.error,
                tools=[run.tool for run in span.tools],
                writes=[write.artifact_id for write in span.writes],
            )
        )

    def on_tool_start(self, tool: str, args: dict[str, Any] | None) -> None:
        if "debug" in self.modes:
            self._put(ToolStart(run_id=self.run_id, tool=tool, args=args))

    def on_tool_end(self, run: Any) -> None:
        if "debug" in self.modes:
            self._put(
                ToolEnd(
                    run_id=self.run_id,
                    tool=run.tool,
                    ok=run.ok,
                    latency_ms=run.latency_ms,
                    error=run.error,
                    chars=run.chars,
                )
            )

    def on_commit(self, commit: Commit) -> None:
        version = commit.context_version or self.context.version
        if "updates" in self.modes:
            self._put(
                ArtifactWrite(
                    run_id=self.run_id,
                    commit_id=commit.id,
                    version=version,
                    author=commit.author,
                    message=commit.message,
                    writes=[write.to_dict() for write in commit.writes],
                )
            )
        if "values" in self.modes:
            try:
                diff = self.context.diff(self._last_version, version)
            except Exception:  # noqa: BLE001 — best-effort, e.g. compacted history
                diff = {}
            self._last_version = version
            self._put(Values(run_id=self.run_id, version=version, diff=diff))

    def on_llm_delta(
        self,
        agent: str,
        step: str,
        delta: str,
        provider: str,
        model: str,
        streamed: bool,
    ) -> None:
        if "messages" in self.modes:
            self._put(
                LLMDelta(
                    run_id=self.run_id,
                    agent=agent,
                    step=step,
                    delta=delta,
                    provider=provider,
                    model=model,
                    streamed=streamed,
                )
            )


__all__ = ["RunEventCollector"]
