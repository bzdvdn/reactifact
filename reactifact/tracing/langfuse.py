"""Langfuse sink: pushes `RunTrace` via OTLP/HTTP (`POST /api/public/otel/v1/traces`).

Langfuse's legacy REST ingestion (`POST /api/public/traces`,
`POST /api/public/observations`) is deprecated: it already 404s/400s on
Langfuse v4, and Langfuse Cloud sunsets it 2026-11-16. OTLP/HTTP is the only
forward-compatible path for a plain HTTP client (not an official SDK) — see
https://langfuse.com/integrations/native/opentelemetry/migration-to-v4.

Mapping: one OTLP span per `AgentSpan` (`langfuse.observation.type=span`), one
child span per `LLMCall` (`type=generation`, `gen_ai.*` attributes for model/
usage). Trace-level attributes (`langfuse.session.id`, trace metadata) are
copied onto every span, since Langfuse only aggregates by them when present on
each observation, not just the root.

Uses httpx (base dependency). For tests you can inject `client`.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from typing import Any

import httpx

from .._httpx import LoopBoundClient
from ._otlp import attr as _attr
from ._otlp import span_id as _span_id
from ._otlp import trace_id as _trace_id
from ._otlp import type_summary as _type_summary
from ._otlp import unix_nanos as _unix_nanos
from .models import AgentSpan, LLMCall, RunTrace
from .tracer import Tracer


class LangfuseTracer(Tracer):
    """Observer that exports traces to Langfuse via OTLP/HTTP."""

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        host: str = "https://cloud.langfuse.com",
        api_url: str | None = None,
        client: Any | None = None,
    ):
        base = (api_url or host).rstrip("/")
        self._url = base + "/api/public/otel/v1/traces"
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "x-langfuse-ingestion-version": "4",
            "Content-Type": "application/json",
        }
        self._http = LoopBoundClient(
            lambda: httpx.AsyncClient(headers=self._headers), client=client
        )

    async def on_turn_end(self, trace: RunTrace) -> None:
        trace_id = _trace_id(trace.id)
        run_start = trace.started_at
        run_end = run_start + timedelta(milliseconds=trace.duration_ms)
        root_span_id = _span_id(f"{trace.id}:root")

        # Propagated to every span: Langfuse only aggregates trace-level
        # fields (session, metadata) when they're present on each observation.
        trace_attrs = [
            _attr("langfuse.trace.name", "reactifact run"),
            _attr("langfuse.trace.metadata.outcome", trace.outcome),
            _attr("langfuse.trace.metadata.span_count", len(trace.spans)),
        ]
        if trace.session_id:
            trace_attrs.append(_attr("langfuse.session.id", trace.session_id))

        spans: list[dict[str, Any]] = [
            {
                "traceId": trace_id,
                "spanId": root_span_id,
                "name": "reactifact run",
                "kind": 1,  # SPAN_KIND_INTERNAL
                "startTimeUnixNano": _unix_nanos(run_start),
                "endTimeUnixNano": _unix_nanos(run_end),
                "attributes": [
                    *trace_attrs,
                    _attr("langfuse.observation.type", "span"),
                ],
            }
        ]

        for i, span in enumerate(trace.spans):
            spans.append(
                self._agent_span(
                    trace_id, root_span_id, trace_attrs, run_start, i, span
                )
            )
            span_start = span.started_at or run_start
            agent_span_id = _span_id(f"{trace_id}:span:{i}:{span.agent}")
            for j, call in enumerate(span.llm_calls):
                spans.append(
                    self._llm_span(
                        trace_id,
                        agent_span_id,
                        trace_attrs,
                        span_start,
                        i,
                        j,
                        span.agent,
                        call,
                    )
                )

        body = {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [{"scope": {"name": "reactifact"}, "spans": spans}],
                }
            ]
        }
        await self._http.get().post(self._url, json=body)

    def _agent_span(
        self,
        trace_id: str,
        parent_span_id: str,
        trace_attrs: list[dict[str, Any]],
        run_start: datetime,
        index: int,
        span: AgentSpan,
    ) -> dict[str, Any]:
        start = span.started_at or run_start
        end = start + timedelta(milliseconds=span.latency_ms)
        attrs = [
            *trace_attrs,
            _attr("langfuse.observation.type", "span"),
            _attr("langfuse.observation.input", [r.model_dump() for r in span.reads]),
            _attr("langfuse.observation.output", [w.model_dump() for w in span.writes]),
            _attr("langfuse.observation.metadata.event_type", span.event_type),
            _attr(
                "langfuse.observation.metadata.read_summary", _type_summary(span.reads)
            ),
            _attr(
                "langfuse.observation.metadata.write_summary",
                _type_summary(span.writes),
            ),
        ]
        if span.error:
            attrs.append(_attr("langfuse.observation.metadata.error", span.error))
        return {
            "traceId": trace_id,
            "spanId": _span_id(f"{trace_id}:span:{index}:{span.agent}"),
            "parentSpanId": parent_span_id,
            "name": span.agent,
            "kind": 1,
            "startTimeUnixNano": _unix_nanos(start),
            "endTimeUnixNano": _unix_nanos(end),
            "attributes": attrs,
        }

    def _llm_span(
        self,
        trace_id: str,
        parent_span_id: str,
        trace_attrs: list[dict[str, Any]],
        span_start: datetime,
        span_index: int,
        call_index: int,
        agent: str,
        call: LLMCall,
    ) -> dict[str, Any]:
        end = span_start + timedelta(milliseconds=call.latency_ms)
        attrs = [
            *trace_attrs,
            _attr("langfuse.observation.type", "generation"),
            _attr("langfuse.observation.input", call.messages),
            _attr("langfuse.observation.output", call.response),
            _attr("gen_ai.request.model", call.model or call.provider),
            _attr("gen_ai.usage.input_tokens", call.prompt_tokens),
            _attr("gen_ai.usage.output_tokens", call.completion_tokens),
            _attr("langfuse.observation.metadata.provider", call.provider),
        ]
        if call.error:
            attrs.append(_attr("langfuse.observation.metadata.error", call.error))
        return {
            "traceId": trace_id,
            "spanId": _span_id(f"{trace_id}:llm:{span_index}:{call_index}:{agent}"),
            "parentSpanId": parent_span_id,
            "name": f"llm:{call.model or call.provider}",
            "kind": 1,
            "startTimeUnixNano": _unix_nanos(span_start),
            "endTimeUnixNano": _unix_nanos(end),
            "attributes": attrs,
        }
