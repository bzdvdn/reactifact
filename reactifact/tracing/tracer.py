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
from .models import AgentSpan, ArtifactRef, LLMCall, RelationRef, RunTrace
from .store import TraceSink, TraceStore

if TYPE_CHECKING:
    from ..agents import Agent
    from ..commit import Read, Write
    from ..context import Context
    from ..events import Event
    from ..patches import Patch
    from ..redaction import Redactor

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


def _clip(value: str, limit: int = TRACE_TRUNCATE) -> str:
    if len(value) <= limit:
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

    Runtime invokes `on_turn_begin` / `on_span` (sync, no I/O) and
    `on_turn_end` (async — the sinks write to SQLite/Postgres/Langfuse).
    `on_turn_end` awaits each sink's `export`, so sinks are async stores.
    """

    def __init__(
        self,
        sink: TraceSink | None = None,
        *,
        store: TraceStore | None = None,
        sinks: Iterable[TraceSink] | None = None,
    ):
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

    def on_span(self, span: AgentSpan) -> None:
        pass

    async def on_turn_end(self, trace: RunTrace) -> None:
        for sink in self.sinks:
            try:
                await sink.export(trace)
            except Exception as exc:  # noqa: BLE001 — one bad sink must not fail the run
                _swallow_tracing_error(f"{type(sink).__name__}.export", exc)


class RecordingLLM(LLMProvider):
    """LLM wrapper: records calls (prompt/response/tokens) into `on_call`."""

    def __init__(
        self,
        inner: LLMProvider,
        on_call: Callable[[LLMCall], None],
        agent_of: Callable[[], str],
        provider: str = "",
        redactor: Redactor | None = None,
    ):
        self._inner = inner
        self._on_call = on_call
        self._agent_of = agent_of
        self._provider = provider or type(inner).__name__
        self._redactor = redactor

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        try:
            response = await self._inner.complete(request)
        except Exception as exc:  # noqa: BLE001 — record and re-raise
            self._safe_record(
                request, None, (time.monotonic() - started) * 1000, str(exc)
            )
            raise
        self._safe_record(request, response, (time.monotonic() - started) * 1000, None)
        return response

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        return self._inner.stream(request)

    def _safe_record(
        self,
        request: LLMRequest,
        response: LLMResponse | None,
        latency_ms: float,
        error: str | None,
    ) -> None:
        """`_record`, isolated: losing one LLMCall must not fail the LLM call.

        The error path matters most: if recording raised while handling the
        provider's own exception, it would mask that original error.
        """
        try:
            self._record(request, response, latency_ms, error)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("RecordingLLM._record", exc)

    def _record(
        self,
        request: LLMRequest,
        response: LLMResponse | None,
        latency_ms: float,
        error: str | None,
    ) -> None:
        usage = dict(response.usage if response is not None else {})
        call = LLMCall(
            agent=self._agent_of(),
            provider=self._provider,
            model=str(getattr(self._inner, "model", "") or ""),
            messages=_clip_messages(request.messages, self._redactor),
            prompt_hash=request.prompt_hash,
            response=(
                _redact(_clip(response.text), self._redactor)
                if response is not None
                else ""
            ),
            prompt_tokens=int(usage.get("prompt_tokens") or usage.get("prompt") or 0),
            completion_tokens=int(
                usage.get("completion_tokens") or usage.get("completion") or 0
            ),
            latency_ms=round(latency_ms, 1),
            error=_redact(error, self._redactor) if error else error,
        )
        self._on_call(call)


class CompositeTracer:
    """Distributes events to all passed observers (local + Langfuse)."""

    def __init__(self, tracers: Iterable[Tracer]):
        self.tracers = list(tracers)

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
    dispatch loop. Owns: wrapping `resources.llm` in `RecordingLLM` on
    construction, the task→agent attribution that wrapper needs to attribute
    an LLM call to the agent that made it, the per-turn span buffer, and the
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
        self.run_id = ""
        self.spans: list[AgentSpan] = []
        #: Wall-clock start of the current turn, used as the trace's `started_at`
        #: (and the origin for the dashboard's timeline).
        self.started_at: datetime | None = None
        if self.tracer is not None and context.resources.llm is not None:
            context.resources.llm = RecordingLLM(
                context.resources.llm,
                on_call=self._record_llm,
                agent_of=self._current_agent_name,
                redactor=self._redactor,
            )

    @property
    def enabled(self) -> bool:
        return self.tracer is not None

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
                        json.dumps(model.model_dump(mode="json"), ensure_ascii=False)
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

    def begin_turn(self, *, session_id: str) -> None:
        if self.tracer is None:
            return
        self.run_id = str(uuid.uuid4())
        self.spans = []
        self._trace_data_cache = {}
        self.started_at = datetime.now(UTC)
        try:
            self.tracer.on_turn_begin(
                self.run_id, session_id=session_id, started_at=datetime.now(UTC)
            )
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_turn_begin", exc)

    def record_span(
        self,
        agent: Agent,
        event: Event,
        reads: list[Read],
        latency_ms: float,
        *,
        error: BaseException | None = None,
    ) -> AgentSpan | None:
        """Builds, buffers and delivers one span — or a no-op if disabled.

        `error is not None` covers the isolated-error path; the success path
        (a span whose `writes`/`relations` are filled in once the patch is
        applied, see `Runtime._commit_patches_to_apply`) passes it as `None`.
        """
        if self.tracer is None:
            return None
        try:
            span = AgentSpan(
                agent=agent.name,
                event_type=event.type.value,
                reads=self.read_refs(reads),
                latency_ms=latency_ms,
                llm_calls=self._pending_llm.pop(agent.name, []),
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
            spans=self.spans,
        )
        try:
            await self.tracer.on_turn_end(trace)
        except Exception as exc:  # noqa: BLE001 — best-effort observability
            _swallow_tracing_error("on_turn_end", exc)
