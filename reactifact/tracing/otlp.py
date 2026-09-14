"""Generic OTLP/HTTP tracer: exports `RunTrace` as spans with GenAI
semantic-convention attributes (https://opentelemetry.io/docs/specs/semconv/gen-ai/)
to any OTLP/HTTP collector — Jaeger, Tempo, Honeycomb, Datadog Agent, or a
local `otel-collector` — no `opentelemetry-sdk` dependency: built on the same
hand-rolled OTLP/HTTP JSON payload `LangfuseTracer` posts over plain `httpx`
(`reactifact.tracing._otlp`), since that's already all a one-shot,
already-finished-run export needs.

Span tree per run — one `invoke_agent` span per `AgentSpan`, one `chat`
child span per `LLMCall`:

    reactifact run
    └── invoke_agent <agent>
        └── chat <model>

Unlike `LangfuseTracer`, every attribute is vendor-neutral: `gen_ai.*` per
the semantic conventions, plus a `reactifact.*` namespace for the
reads/writes/provenance data specific to reactifact's artifact model — no
`langfuse.*` keys, so any OTLP-consuming backend renders it the same way.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ._otlp import attr, span_id, trace_id, type_summary, unix_nanos
from .models import AgentSpan, LLMCall, RunTrace
from .tracer import Tracer


class OTLPTracer(Tracer):
    """Observer that exports traces to any OTLP/HTTP collector.

    `endpoint` is the collector's traces path, e.g.
    `http://localhost:4318/v1/traces` for a local `otel-collector`, or your
    vendor's OTLP/HTTP ingestion URL. Pass `headers` for one that requires
    auth (most hosted backends do).
    """

    def __init__(
        self,
        *,
        endpoint: str,
        headers: dict[str, str] | None = None,
        service_name: str = "reactifact",
        client: Any | None = None,
    ):
        self._url = endpoint
        self._service_name = service_name
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        if client is not None:
            self._client = client
        else:
            import httpx

            self._client = httpx.AsyncClient(headers=self._headers)

    async def on_turn_end(self, trace: RunTrace) -> None:
        tid = trace_id(trace.id)
        run_start = trace.started_at
        run_end = run_start + timedelta(milliseconds=trace.duration_ms)
        root_span_id = span_id(f"{trace.id}:root")

        spans: list[dict[str, Any]] = [
            {
                "traceId": tid,
                "spanId": root_span_id,
                "name": "reactifact run",
                "kind": 1,  # SPAN_KIND_INTERNAL
                "startTimeUnixNano": unix_nanos(run_start),
                "endTimeUnixNano": unix_nanos(run_end),
                "attributes": [
                    attr("gen_ai.operation.name", "invoke_agent"),
                    attr("reactifact.run.outcome", trace.outcome),
                    attr("reactifact.run.span_count", len(trace.spans)),
                    *(
                        [attr("reactifact.session.id", trace.session_id)]
                        if trace.session_id
                        else []
                    ),
                ],
            }
        ]

        for i, span in enumerate(trace.spans):
            spans.append(self._agent_span(tid, root_span_id, run_start, i, span))
            span_start = span.started_at or run_start
            agent_span_id = span_id(f"{tid}:span:{i}:{span.agent}")
            for j, call in enumerate(span.llm_calls):
                spans.append(
                    self._llm_span(
                        tid, agent_span_id, span_start, i, j, span.agent, call
                    )
                )

        body = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [attr("service.name", self._service_name)]
                    },
                    "scopeSpans": [{"scope": {"name": "reactifact"}, "spans": spans}],
                }
            ]
        }
        await self._client.post(self._url, json=body)

    def _agent_span(
        self,
        tid: str,
        parent_span_id: str,
        run_start: datetime,
        index: int,
        span: AgentSpan,
    ) -> dict[str, Any]:
        start = span.started_at or run_start
        end = start + timedelta(milliseconds=span.latency_ms)
        attrs = [
            attr("gen_ai.operation.name", "invoke_agent"),
            attr("gen_ai.agent.name", span.agent),
            attr("reactifact.event_type", span.event_type),
            attr("reactifact.reads", type_summary(span.reads)),
            attr("reactifact.writes", type_summary(span.writes)),
        ]
        if span.error:
            attrs.append(attr("error.type", span.error))
        return {
            "traceId": tid,
            "spanId": span_id(f"{tid}:span:{index}:{span.agent}"),
            "parentSpanId": parent_span_id,
            "name": f"invoke_agent {span.agent}",
            "kind": 1,
            "startTimeUnixNano": unix_nanos(start),
            "endTimeUnixNano": unix_nanos(end),
            "attributes": attrs,
        }

    def _llm_span(
        self,
        tid: str,
        parent_span_id: str,
        span_start: datetime,
        span_index: int,
        call_index: int,
        agent: str,
        call: LLMCall,
    ) -> dict[str, Any]:
        end = span_start + timedelta(milliseconds=call.latency_ms)
        attrs = [
            attr("gen_ai.operation.name", "chat"),
            attr("gen_ai.system", call.provider),
            attr("gen_ai.request.model", call.model or call.provider),
            attr("gen_ai.usage.input_tokens", call.prompt_tokens),
            attr("gen_ai.usage.output_tokens", call.completion_tokens),
            attr("gen_ai.agent.name", agent),
        ]
        if call.error:
            attrs.append(attr("error.type", call.error))
        return {
            "traceId": tid,
            "spanId": span_id(f"{tid}:llm:{span_index}:{call_index}:{agent}"),
            "parentSpanId": parent_span_id,
            "name": f"chat {call.model or call.provider}",
            "kind": 1,
            "startTimeUnixNano": unix_nanos(span_start),
            "endTimeUnixNano": unix_nanos(end),
            "attributes": attrs,
        }
