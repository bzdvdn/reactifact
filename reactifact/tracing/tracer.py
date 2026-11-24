"""Trace delivery: `Tracer` (observer) and `CompositeTracer` (fan-out).

`Tracer` is what the Runtime sees as the source of run events. It is configured
with one or more sinks (`TraceStore`, later Langfuse/Postgres) and on
`on_turn_end` pushes the collected `RunTrace` to each sink:

    Runtime(ctx, agents, tracer=Tracer(store=TraceStore("traces.db")))

`RecordingLLM` is a wrapper over the LLM provider: while tracing is enabled, it intercepts
`complete()` and writes `LLMCall` (tokens from `usage`, attribution to the agent via
`asyncio.current_task()`). Producers know nothing about it — they simply
call `context.resources.llm` as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..patches import Create, Link, Update
from ..providers import LLMProvider, LLMRequest, LLMResponse, LLMResponseChunk
from ..tools import ToolRun, current_produce_name
from .models import (
    AgentContract,
    AgentSpan,
    ArtifactRef,
    LLMCall,
    RelationRef,
    RunTrace,
    StepRun,
    TraceLimits,
)
from .store import TraceSink, TraceStore

if TYPE_CHECKING:
    from ..commit import Commit, Read, Write
    from ..context import Context
    from ..events import Event
    from ..patches import Patch
    from ..redaction import Redactor
    from ..step import StepGroup

logger = logging.getLogger(__name__)

#: Up to what size to truncate artifact/response data in a trace.
TRACE_TRUNCATE = 1500


def _swallow_tracing_error(where: str, exc: BaseException) -> None:
    """Reports a tracing failure and lets the run continue (§54).

    Observability is best-effort by contract: a sink that is unreachable
    (Langfuse down, Postgres refusing connections), a custom `Tracer`
    callback that raises, or a malformed payload must never abort the
    business run it was only supposed to observe. Every tracer call site
    funnels its exceptions here and carries on; the trace for that step is
    simply dropped. `exc_info` keeps the failure debuggable in logs.
    """
    logger.warning(
        "tracing step %r failed; run continues without it: %s", where, exc, exc_info=exc
    )


def _redact(value: str, redactor: Redactor | None) -> str:
    """Applies `redactor` to trace text, best-effort.

    Same contract as sinks: a redactor that raises must not abort the run it
    was only supposed to sanitize — the original text is kept instead.
    """
    if redactor is None or not value:
        return value
    try:
        return redactor.redact(value)
    except Exception as exc:  # noqa: BLE001 — best-effort observability
        _swallow_tracing_error("redactor.redact", exc)
        return value


def _clip(value: str, limit: int | None = TRACE_TRUNCATE) -> str:
    if limit is None or len(value) <= limit:
        return value
    return value[:limit] + "…"


def _message_fields(message: Any) -> tuple[str, Any]:
    """Reads role/content off a `Message` dataclass, dict, or pydantic-like object.

    `LLMRequest.messages` is typed `list[Message]` (a plain dataclass without
    `model_dump`), but providers/hand-rolled callers can also hand over dicts or
    pydantic models — only these two fields are ever traced, so attribute access
    covers all of them without assuming a serialization method.
    """
    if isinstance(message, dict):
        return str(message.get("role", "")), message.get("content")
    return str(getattr(message, "role", "")), getattr(message, "content", None)


def _clip_messages(
    messages: Iterable[Any], redactor: Redactor | None = None
) -> list[dict[str, Any]]:
    clipped: list[dict[str, Any]] = []
    for message in messages:
        role, content = _message_fields(message)
        text = _clip(str(content or ""))
        clipped.append({"role": role, "content": _redact(text, redactor)})
    return clipped


class Tracer:
    """Observer of runs for the Runtime (§54).

    Runtime invokes `on_turn_begin` / `on_agent_start` / `on_tool_start` /
    `on_tool_end` / `on_span` / `on_commit` (sync, no I/O) and `on_turn_end`
    (async — the sinks write to SQLite/Postgres/Langfuse). `on_turn_end`
    awaits each sink's `export`, so sinks are async stores.

    The lifecycle hooks are intentionally best-effort and non-blocking: a
    live UI (a Studio event log) can consume them, but must enqueue rather
    than do I/O in the callback. The finished `RunTrace` remains the source
    of truth; these hooks only make its construction observable.
    """

    def __init__(
        self,
        sink: TraceSink | None = None,
        *,
        store: TraceStore | None = None,
        sinks: Iterable[TraceSink] | None = None,
        limits: TraceLimits | None = None,
    ):
        self.limits = limits or TraceLimits()
        self.sinks: list[TraceSink] = []
        if sink is not None:
            self.sinks.append(sink)
        if store is not None:
            self.sinks.append(store)
        if sinks is not None:
            self.sinks.extend(sinks)

    def on_turn_begin(
        self, run_id: str, *, session_id: str, started_at: datetime
    ) -> None:
        pass

    def on_generation_start(self, run_id: str, generation: int) -> None:
        """Called when a new generation starts inside the current turn."""
        pass

    def on_llm_delta(
        self,
        agent: str,
        step: str,
        delta: str,
        provider: str,
        model: str,
        streamed: bool,
    ) -> None:
        """Called for streamed (or one-shot completed) LLM text.

        Only tracers that opt in via `wants_llm_deltas` receive it, so a normal
        `TraceStore`/metrics setup pays nothing per token.
        """
        pass

    @property
    def wants_llm_deltas(self) -> bool:
        """Whether this tracer needs `on_llm_delta` (opt-in; default `False`)."""
        return False

    def on_agent_start(self, agent: StepGroup, event: Event, reads: list[Read]) -> None:
        """Called before an agent's `run()` (after authorization)."""
        pass

    def on_agent_retry(
        self, agent: StepGroup, attempt: int, error: BaseException
    ) -> None:
        """Called after a failed attempt when the agent will be retried."""
        pass

    def on_tool_start(self, tool: str, args: dict[str, Any] | None) -> None:
        """Called before a tool executes; `args` is redacted or `None`."""
        pass

    def on_tool_end(self, run: ToolRun) -> None:
        """Called after a tool finishes (success or error)."""
        pass

    def on_commit(self, commit: Commit) -> None:
        """Called after a generation's patch is committed."""
        pass

    def on_span(self, span: AgentSpan) -> None:
        pass

    async def on_turn_end(self, trace: RunTrace) -> None:
        for sink in self.sinks:
            try:
                await sink.export(trace)
            except Exception as exc:  # noqa: BLE001 — one bad sink must not fail the run
                _swallow_tracing_error(f"{type(sink).__name__}.export", exc)


class RecordingLLM(LLMProvider):
    """LLM wrapper: records calls (prompt/response/tokens) into `on_call`.

    With `on_delta`, streamed chunks are also forwarded as they arrive (and a
    non-streamed `complete()` emits one final delta). The callback is opt-in
    (`Tracer.wants_llm_deltas`), sync, and best-effort: it must not block.
    """

    def __init__(
        self,
        inner: LLMProvider,
        on_call: Callable[[LLMCall], None],
        agent_of: Callable[[], str],
        provider: str = "",
        redactor: Redactor | None = None,
        truncate: int | None = TRACE_TRUNCATE,
        on_delta: Callable[[str, str, str, bool], None] | None = None,
    ):
        self._inner = inner
        self._on_call = on_call
        self._agent_of = agent_of
        self._provider = provider or type(inner).__name__
        self._model = str(getattr(inner, "model", "") or "")
        self._redactor = redactor
        self._truncate = truncate
        self._on_delta = on_delta

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            response = await self._inner.complete(request)
        except Exception as exc:  # noqa: BLE001 — record and re-raise
            self._safe_record(
                request,
                None,
                (time.monotonic() - started) * 1000,
                str(exc),
                started_at,
            )
            raise
        self._safe_delta(response.text, streamed=False)
        self._safe_record(
            request,
            response,
            (time.monotonic() - started) * 1000,
            None,
            started_at,
        )
        return response

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        """Yields the provider's chunks and records one `LLMCall` at the end.

        The extra layer is deliberately cheap: chunks are appended to a list
        (never `text +=`), the buffer is capped at `truncate`, and `_record`
        runs once — in `finally` — not per chunk. When `on_delta` is set, each
        chunk is forwarded (sync, best-effort) for a live UI.
        """
        started_at = datetime.now(UTC)
        started = time.monotonic()
        parts: list[str] = []
        size = 0
        limit = self._truncate
        truncated = False
        usage: dict[str, Any] = {}
        error: str | None = None
        try:
            async for chunk in self._inner.stream(request):
                if chunk.usage:
                    usage.update(chunk.usage)
                if chunk.text:
                    if limit is None:
                        parts.append(chunk.text)
                    elif size < limit:
                        take = chunk.text[: limit - size]
                        parts.append(take)
                        size += len(take)
                        if len(take) < len(chunk.text):
                            truncated = True
                    else:
                        truncated = True
                if chunk.text:
                    self._safe_delta(chunk.text, streamed=True)
                yield chunk
        except asyncio.CancelledError:
            error = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — record and re-raise
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            text = "".join(parts)
            if truncated and limit is not None:
                # `_record` clips to `limit` and adds the ellipsis itself;
                # passing the marker through keeps that single clip honest.
                text += "…"
            self._safe_record(
                request,
                LLMResponse(text=text, usage=usage),
                (time.monotonic() - started) * 1000,
                error,
                started_at,
            )

    def _safe_record(
        self,
        request: LLMRequest,
        response: LLMResponse | None,
        latency_ms: float,
        error: str | None,
        started_at: datetime,
    ) -> None:
        """`_record`, isolated: losing one LLMCall must not fail the LLM call.

        The error path matters most: if recording raised while handling the
        provider's own exception, it would mask that original error.
        """
        try:
            self._record(request, response, latency_ms, error, started_at)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("RecordingLLM._record", exc)

    def _safe_delta(self, text: str, *, streamed: bool) -> None:
        """Forwards one LLM text chunk, isolated from the provider call."""
        if self._on_delta is None or not text:
            return
        try:
            self._on_delta(text, self._provider, self._model, streamed)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("RecordingLLM._delta", exc)

    def _record(
        self,
        request: LLMRequest,
        response: LLMResponse | None,
        latency_ms: float,
        error: str | None,
        started_at: datetime,
    ) -> None:
        usage = dict(response.usage if response is not None else {})
        call = LLMCall(
            agent=self._agent_of(),
            step=current_produce_name(),
            provider=self._provider,
            model=self._model,
            messages=_clip_messages(request.messages, self._redactor),
            prompt_hash=request.prompt_hash,
            response=(
                _redact(_clip(response.text, self._truncate), self._redactor)
                if response is not None
                else ""
            ),
            prompt_tokens=int(usage.get("prompt_tokens") or usage.get("prompt") or 0),
            completion_tokens=int(
                usage.get("completion_tokens") or usage.get("completion") or 0
            ),
            latency_ms=round(latency_ms, 1),
            started_at=started_at,
            error=_redact(error, self._redactor) if error else error,
        )
        self._on_call(call)


class CompositeTracer:
    """Distributes events to all passed observers (local + Langfuse)."""

    def __init__(
        self,
        tracers: Iterable[Tracer],
        *,
        limits: TraceLimits | None = None,
    ):
        self.tracers = list(tracers)
        self.limits = limits or TraceLimits()

    def on_turn_begin(
        self, run_id: str, *, session_id: str, started_at: datetime
    ) -> None:
        for tracer in self.tracers:
            try:
                tracer.on_turn_begin(
                    run_id, session_id=session_id, started_at=started_at
                )
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(f"{type(tracer).__name__}.on_turn_begin", exc)

    def on_generation_start(self, run_id: str, generation: int) -> None:
        for tracer in self.tracers:
            try:
                tracer.on_generation_start(run_id, generation)
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(
                    f"{type(tracer).__name__}.on_generation_start", exc
                )

    def on_llm_delta(
        self,
        agent: str,
        step: str,
        delta: str,
        provider: str,
        model: str,
        streamed: bool,
    ) -> None:
        for tracer in self.tracers:
            try:
                tracer.on_llm_delta(agent, step, delta, provider, model, streamed)
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(f"{type(tracer).__name__}.on_llm_delta", exc)

    @property
    def wants_llm_deltas(self) -> bool:
        return any(
            getattr(tracer, "wants_llm_deltas", False) for tracer in self.tracers
        )

    def on_agent_start(self, agent: StepGroup, event: Event, reads: list[Read]) -> None:
        for tracer in self.tracers:
            try:
                tracer.on_agent_start(agent, event, reads)
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(f"{type(tracer).__name__}.on_agent_start", exc)

    def on_agent_retry(
        self, agent: StepGroup, attempt: int, error: BaseException
    ) -> None:
        for tracer in self.tracers:
            try:
                tracer.on_agent_retry(agent, attempt, error)
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(f"{type(tracer).__name__}.on_agent_retry", exc)

    def on_tool_start(self, tool: str, args: dict[str, Any] | None) -> None:
        for tracer in self.tracers:
            try:
                tracer.on_tool_start(tool, args)
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(f"{type(tracer).__name__}.on_tool_start", exc)

    def on_tool_end(self, run: ToolRun) -> None:
        for tracer in self.tracers:
            try:
                tracer.on_tool_end(run)
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(f"{type(tracer).__name__}.on_tool_end", exc)

    def on_commit(self, commit: Commit) -> None:
        for tracer in self.tracers:
            try:
                tracer.on_commit(commit)
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(f"{type(tracer).__name__}.on_commit", exc)

    def on_span(self, span: AgentSpan) -> None:
        for tracer in self.tracers:
            try:
                tracer.on_span(span)
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(f"{type(tracer).__name__}.on_span", exc)

    async def on_turn_end(self, trace: RunTrace) -> None:
        for tracer in self.tracers:
            try:
                await tracer.on_turn_end(trace)
            except Exception as exc:  # noqa: BLE001 — isolate one bad tracer
                _swallow_tracing_error(f"{type(tracer).__name__}.on_turn_end", exc)


class RunTracer:
    """Runtime's tracing collaborator (§54).

    Builds `ArtifactRef`/`RelationRef`/`AgentSpan`/`RunTrace` and delivers
    them to the configured `Tracer`, so `Runtime` only calls into this at a
    few well-defined points instead of interleaving span-building with the
    dispatch loop. Owns: wrapping `resources.llm` in `RecordingLLM` at turn
    start (on the runtime's per-turn resources copy), the task→agent
    attribution that wrapper needs to attribute an LLM call to the agent that
    made it, the per-turn span buffer, and the
    artifact-data truncation cache (`ArtifactRef.data` is memoized per
    `(artifact_id, version)` for one turn, since the same artifact is often
    referenced — as a read — by several spans in a generation).

    A no-op when `tracer` is None (the common case, no tracing configured):
    every method still works, `record_span` just returns `None` and nothing
    is buffered or sent anywhere.
    """

    def __init__(self, context: Context, tracer: Tracer | CompositeTracer | None):
        self._context = context
        self.tracer = tracer
        self._agent_by_task: dict[asyncio.Task[Any], str] = {}
        self._pending_llm: dict[str, list[LLMCall]] = {}
        self._trace_data_cache: dict[tuple[str, int], str] = {}
        self._redactor = getattr(context.resources, "redactor", None)
        self._truncate = getattr(context.resources, "trace_truncate", TRACE_TRUNCATE)
        self.run_id = ""
        self.spans: list[AgentSpan] = []
        #: Workspace/scope of this run (copied onto `RunTrace.scope`), so a
        #: self-hosted dashboard can filter traces per workspace.
        self.scope = ""
        #: Parent run (subflow/branch) and branch label, copied onto `RunTrace`.
        self.parent_run_id = ""
        self.branch = ""
        #: App-provided run metadata (copied onto `RunTrace.metadata`).
        self.metadata: dict[str, str] = {}
        #: Wall-clock start of the current turn, used as the trace's `started_at`
        #: (and the origin for the dashboard's timeline).
        self.started_at: datetime | None = None
        #: Write-time trace caps (see `TraceLimits` on the tracer). When a
        #: cap is hit, the span/ref/call is dropped and the trace is marked
        #: truncated instead of letting a runaway run exhaust memory.
        limits = getattr(tracer, "limits", None) or TraceLimits()
        self._max_spans = limits.max_spans
        self._max_refs = limits.max_refs_per_span
        self._max_llm = limits.max_llm_calls_per_span
        if limits.max_data_chars is not None:
            self._truncate = limits.max_data_chars
        self.truncated = False

    @property
    def enabled(self) -> bool:
        return self.tracer is not None

    @property
    def data_char_limit(self) -> int | None:
        """The resolved per-text trace cap (`TraceLimits.max_data_chars` or the
        legacy `RuntimeResources.trace_truncate` fallback)."""
        return self._truncate

    # ---- task -> agent attribution, for RecordingLLM.agent_of ----

    def register_task(self, task: asyncio.Task[Any] | None, agent_name: str) -> None:
        if task is not None:
            self._agent_by_task[task] = agent_name

    def unregister_task(self, task: asyncio.Task[Any] | None) -> None:
        if task is not None:
            self._agent_by_task.pop(task, None)

    def _current_agent_name(self) -> str:
        task = asyncio.current_task()
        if task is None:
            return ""
        return self._agent_by_task.get(task, "")

    def _record_llm(self, call: LLMCall) -> None:
        self._pending_llm.setdefault(call.agent, []).append(call)

    def _record_llm_delta(
        self, delta: str, provider: str, model: str, streamed: bool
    ) -> None:
        """Forwards a live LLM chunk to tracers that opted into deltas."""
        if self.tracer is None:
            return
        try:
            self.tracer.on_llm_delta(
                self._current_agent_name(),
                current_produce_name(),
                delta,
                provider,
                model,
                streamed,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_llm_delta", exc)

    # ---- ArtifactRef / RelationRef builders ----

    def _artifact_data(self, artifact_id: str) -> Any | None:
        artifact = self._context.get(artifact_id)
        return artifact.data if artifact is not None else None

    def artifact_ref(
        self,
        artifact_id: str,
        version: int,
        op_type: str,
        model: Any | None,
    ) -> ArtifactRef:
        data: str | None = None
        if model is not None:
            key = (artifact_id, version)
            data = self._trace_data_cache.get(key)
            if data is None:
                data = _redact(
                    _clip(
                        json.dumps(model.model_dump(mode="json"), ensure_ascii=False),
                        self._truncate,
                    ),
                    self._redactor,
                )
                self._trace_data_cache[key] = data
        return ArtifactRef(
            artifact_id=artifact_id,
            version=version,
            op_type=op_type,
            data_type=type(model).__name__ if model is not None else "",
            data=data,
        )

    @staticmethod
    def _type_name(artifact_id: str, context: Context | None) -> str:
        artifact = context.get(artifact_id) if context is not None else None
        data = artifact.data if artifact is not None else None
        return type(data).__name__ if data is not None else ""

    def read_refs(self, reads: list[Read]) -> list[ArtifactRef]:
        return [
            self.artifact_ref(
                read.artifact_id,
                read.version,
                "read",
                self._artifact_data(read.artifact_id),
            )
            for read in reads
        ]

    def write_refs(self, patch: Patch, writes: list[Write]) -> list[ArtifactRef]:
        try:
            if self._max_refs is not None and len(writes) > self._max_refs:
                self.truncated = True
                writes = writes[: self._max_refs]
            ops_by_id: dict[str, tuple[str, Any | None]] = {}
            for op in patch.operations:
                artifact_id = getattr(op, "artifact_id", None)
                if artifact_id is None:
                    continue
                if isinstance(op, Create):
                    model: Any | None = op.data
                elif isinstance(op, Update):
                    model = op.new_data
                else:
                    model = None
                ops_by_id[artifact_id] = (op.to_dict().get("type", ""), model)
            return [
                self.artifact_ref(
                    w.artifact_id,
                    w.version,
                    ops_by_id.get(w.artifact_id, ("", None))[0],
                    ops_by_id.get(w.artifact_id, ("", None))[1],
                )
                for w in writes
            ]
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("write_refs", exc)
            return []

    def relation_refs(self, patch: Patch) -> list[RelationRef]:
        """Provenance edges (`patch.link`) recorded for the span (§34)."""
        try:
            refs: list[RelationRef] = []
            for op in patch.operations:
                if not isinstance(op, Link):
                    continue
                if self._max_refs is not None and len(refs) >= self._max_refs:
                    self.truncated = True
                    break
                refs.append(
                    RelationRef(
                        source_id=op.artifact_id,
                        relation=op.relation,
                        target_id=op.target_id,
                        source_type=self._type_name(op.artifact_id, self._context),
                        target_type=self._type_name(op.target_id, self._context),
                    )
                )
            return refs
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("relation_refs", exc)
            return []

    # ---- per-turn lifecycle ----

    def begin_turn(
        self,
        *,
        session_id: str,
        scope: str = "",
        metadata: dict[str, str] | None = None,
        parent_run_id: str = "",
        branch: str = "",
    ) -> None:
        if self.tracer is None:
            return
        # Install the recording wrapper at turn start, not at Runtime
        # construction: the Runtime copies `resources` for the turn (see
        # `Runtime._begin_turn`), so wrapping the copy keeps a resources
        # instance shared across sessions/turns free of stacked wrappers.
        llm = self._context.resources.llm
        if llm is not None:
            wants_deltas = getattr(self.tracer, "wants_llm_deltas", False)
            self._context.resources.llm = RecordingLLM(
                llm,
                on_call=self._record_llm,
                agent_of=self._current_agent_name,
                redactor=self._redactor,
                truncate=self._truncate,
                on_delta=self._record_llm_delta if wants_deltas else None,
            )
        self.run_id = str(uuid.uuid4())
        self.spans = []
        self.truncated = False
        self.scope = scope
        self.parent_run_id = parent_run_id
        self.branch = branch
        self.metadata = dict(metadata or {})
        self._trace_data_cache = {}
        self.started_at = datetime.now(UTC)
        try:
            self.tracer.on_turn_begin(
                self.run_id, session_id=session_id, started_at=datetime.now(UTC)
            )
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_turn_begin", exc)

    def generation_started(self, generation: int) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.on_generation_start(self.run_id, generation)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_generation_start", exc)

    def agent_started(self, agent: StepGroup, event: Event, reads: list[Read]) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.on_agent_start(agent, event, reads)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_agent_start", exc)

    def agent_retried(
        self, agent: StepGroup, attempt: int, error: BaseException
    ) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.on_agent_retry(agent, attempt, error)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_agent_retry", exc)

    def tool_started(self, tool: str, args: dict[str, Any] | None) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.on_tool_start(tool, args)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_tool_start", exc)

    def tool_ended(self, run: ToolRun) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.on_tool_end(run)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_tool_end", exc)

    def committed(self, commit: Commit) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.on_commit(commit)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_commit", exc)

    def record_span(
        self,
        agent: StepGroup,
        event: Event,
        reads: list[Read],
        latency_ms: float,
        *,
        error: BaseException | None = None,
        produces: list[tuple[str, int, float, datetime]] | None = None,
        tools: list[ToolRun] | None = None,
    ) -> AgentSpan | None:
        """Builds, buffers and delivers one span — or a no-op if disabled.

        `error is not None` covers the isolated-error path; the success path
        (a span whose `writes`/`relations` are filled in once the patch is
        applied, see `Runtime._commit_patches_to_apply`) passes it as `None`.
        `produces` is the ``(name, operations, latency_ms, started_at)`` list the agent's
        `execute()` recorded for this event.
        """
        if self.tracer is None:
            return None
        if self._max_spans is not None and len(self.spans) >= self._max_spans:
            self.truncated = True
            return None
        try:
            if self._max_refs is not None and len(reads) > self._max_refs:
                self.truncated = True
                reads = reads[: self._max_refs]
            llm_calls = self._pending_llm.pop(agent.name, [])
            if self._max_llm is not None and len(llm_calls) > self._max_llm:
                self.truncated = True
                llm_calls = llm_calls[: self._max_llm]
            if self._max_refs is not None and tools is not None:
                tools = tools[: self._max_refs]
            consumes = [
                getattr(c.artifact_type, "__name__", str(c.artifact_type))
                for c in (agent.consumes or [])
                if c.artifact_type is not None
            ]
            produces_declared = [
                getattr(p, "name", type(p).__name__) for p in (agent.steps or [])
            ]
            declared_tools = getattr(agent, "tools", ()) or ()
            if isinstance(declared_tools, dict):
                declared_tools = declared_tools.values()
            tools_declared = [
                getattr(t, "name", type(t).__name__) for t in declared_tools
            ]
            span = AgentSpan(
                agent=agent.name,
                event_type=event.type.value,
                event_artifact_type=(
                    event.artifact_type.__name__
                    if isinstance(event.artifact_type, type)
                    else str(event.artifact_type)
                ),
                event_artifact_id=event.artifact_id,
                contract=AgentContract(
                    consumes=consumes,
                    produces=produces_declared,
                    tools=tools_declared,
                    priority=agent.priority,
                ),
                produces=[
                    StepRun(
                        name=name,
                        operations=operations,
                        latency_ms=latency,
                        started_at=started_at,
                    )
                    for name, operations, latency, started_at in (produces or [])
                ],
                tools=tools or [],
                reads=self.read_refs(reads),
                latency_ms=latency_ms,
                llm_calls=llm_calls,
                error=(
                    _redact(f"{type(error).__name__}: {error}", self._redactor)
                    if error is not None
                    else None
                ),
                # The span is recorded after its agent finished (delivery is
                # once per generation), so the true start is the record time
                # minus the measured latency — this keeps parallel agents'
                # bars side by side on the dashboard timeline.
                started_at=datetime.now(UTC) - timedelta(milliseconds=latency_ms),
            )
            self.spans.append(span)
            self.tracer.on_span(span)
            return span
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_span", exc)
            return None

    async def end_turn(
        self, *, session_id: str, duration_ms: float, outcome: str
    ) -> None:
        if self.tracer is None:
            return
        trace = RunTrace(
            id=self.run_id,
            session_id=session_id,
            started_at=self.started_at or datetime.now(UTC),
            duration_ms=duration_ms,
            outcome=outcome,
            scope=self.scope,
            parent_run_id=self.parent_run_id,
            branch=self.branch,
            metadata=self.metadata,
            spans=self.spans,
            trace_truncated=self.truncated,
        )
        try:
            await self.tracer.on_turn_end(trace)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_turn_end", exc)
