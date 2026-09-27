"""reactifact.metrics — dependency-free Prometheus metrics for runs.

`Metrics` is a tiny in-process collector (counters + histograms) and
`MetricsTracer` fills it from the same hook the tracing sinks use — so a run
already produces the operational numbers with no extra plumbing:

    from reactifact.metrics import Metrics, MetricsTracer, create_metrics_router

    metrics = Metrics()
    runtime = Runtime(ctx, agents=AGENTS, tracer=MetricsTracer(metrics))
    app.include_router(create_metrics_router(metrics))   # GET /metrics

Counters: `reactifact_runs_total{outcome}`,
`reactifact_budget_exceeded_total{outcome}`,
`reactifact_agent_runs_total{agent,status}`,
`reactifact_llm_calls_total{provider,model,agent}`,
`reactifact_llm_tokens_total{kind,provider,model}`,
`reactifact_llm_errors_total{provider,model}`, and (with a `pricer`)
`reactifact_llm_cost_total{provider,model}`. Histograms:
`reactifact_agent_latency_seconds{agent}`, `reactifact_llm_latency_seconds`.

No `prometheus_client` dependency: `Metrics.render()` emits the Prometheus text
exposition format directly, and `create_metrics_router` serves it (the `web`
extra). `MetricsTracer` can also be combined with the trace sinks —
`tracer=[TraceStore(...), MetricsTracer(metrics)]`.
"""

from __future__ import annotations

from typing import Any

from .pricing import Pricer, cost_of
from .tracing.models import AgentSpan, LLMCall, RunTrace
from .tracing.tracer import Tracer

#: Default latency buckets, in seconds.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

_Labels = tuple[tuple[str, str], ...]
_HELP = {
    "reactifact_runs_total": "Turns run, by outcome.",
    "reactifact_budget_exceeded_total": "Turns stopped by a Budget limit, by outcome.",
    "reactifact_agent_runs_total": "Agent executions, by agent and status.",
    "reactifact_agent_latency_seconds": "Agent execution latency.",
    "reactifact_llm_calls_total": "LLM calls, by provider/model/agent.",
    "reactifact_llm_tokens_total": "LLM tokens, by kind (prompt/completion).",
    "reactifact_llm_errors_total": "LLM calls that returned an error.",
    "reactifact_llm_cost_total": "LLM cost (via the injected pricer).",
    "reactifact_llm_latency_seconds": "LLM call latency.",
    "reactifact_artifacts_written_total": "Artifacts written, by type and op.",
    "reactifact_artifacts_read_total": "Artifacts read, by type.",
    "reactifact_relations_total": "Provenance edges added, by relation.",
}


class _Histogram:
    def __init__(self, buckets: tuple[float, ...]) -> None:
        self.buckets = buckets
        #: Cumulative counts per bucket (value <= bucket), then the +Inf count.
        self.counts = [0] * len(buckets)
        self.count = 0
        self.total = 0.0

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        for index, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[index] += 1


class Metrics:
    """An in-process Prometheus collector: `increment` / `observe` / `render`."""

    def __init__(self, *, buckets: tuple[float, ...] = DEFAULT_BUCKETS):
        self._buckets = tuple(buckets)
        self._counters: dict[tuple[str, _Labels], float] = {}
        self._histograms: dict[tuple[str, _Labels], _Histogram] = {}

    @staticmethod
    def _labels(labels: dict[str, Any]) -> _Labels:
        return tuple(sorted((k, str(v)) for k, v in labels.items()))

    def increment(self, name: str, value: float = 1.0, **labels: Any) -> None:
        key = (name, self._labels(labels))
        self._counters[key] = self._counters.get(key, 0.0) + value

    def observe(self, name: str, value: float, **labels: Any) -> None:
        key = (name, self._labels(labels))
        histogram = self._histograms.get(key)
        if histogram is None:
            histogram = _Histogram(self._buckets)
            self._histograms[key] = histogram
        histogram.observe(value)

    def reset(self) -> None:
        self._counters.clear()
        self._histograms.clear()

    def render(self) -> str:
        """The Prometheus text exposition of the current metrics."""
        lines: list[str] = []
        for name in sorted({n for n, _ in (*self._counters, *self._histograms)}):
            help_text = _HELP.get(name)
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            kind = (
                "histogram"
                if any(n == name for n, _ in self._histograms)
                else "counter"
            )
            lines.append(f"# TYPE {name} {kind}")
            if kind == "counter":
                lines.extend(self._render_counter(name))
            else:
                lines.extend(self._render_histogram(name))
        return "\n".join(lines) + "\n"

    def _render_counter(self, name: str) -> list[str]:
        rows = [
            (labels, value)
            for (metric, labels), value in self._counters.items()
            if metric == name
        ]
        return [
            f"{name}{_fmt_labels(labels)} {_fmt(value)}"
            for labels, value in sorted(rows)
        ]

    def _render_histogram(self, name: str) -> list[str]:
        out: list[str] = []
        rows = sorted(
            (labels, hist)
            for (metric, labels), hist in self._histograms.items()
            if metric == name
        )
        for labels, hist in rows:
            for bound, count in zip(self._buckets, hist.counts, strict=True):
                out.append(
                    f"{name}_bucket{_fmt_labels(labels, le=_fmt(bound))} {count}"
                )
            out.append(f"{name}_bucket{_fmt_labels(labels, le='+Inf')} {hist.count}")
            out.append(f"{name}_sum{_fmt_labels(labels)} {_fmt(hist.total)}")
            out.append(f"{name}_count{_fmt_labels(labels)} {hist.count}")
        return out


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt_labels(labels: _Labels, **extra: str) -> str:
    pairs = list(labels) + list(extra.items())
    if not pairs:
        return ""
    # Alphabetical, but `le` last (the Prometheus histogram convention).
    pairs.sort(key=lambda kv: (kv[0] == "le", kv[0]))
    inner = ",".join(f'{key}="{_escape(value)}"' for key, value in pairs)
    return "{" + inner + "}"


class MetricsTracer(Tracer):
    """A `Tracer` that records Prometheus metrics from each finished turn."""

    def __init__(self, metrics: Metrics, *, pricer: Pricer | None = None):
        super().__init__()
        self.metrics = metrics
        self._pricer = pricer

    async def on_turn_end(self, trace: RunTrace) -> None:
        await super().on_turn_end(trace)
        metrics = self.metrics
        metrics.increment("reactifact_runs_total", outcome=trace.outcome or "unknown")
        if trace.outcome.startswith("budget_"):
            metrics.increment("reactifact_budget_exceeded_total", outcome=trace.outcome)
        for span in trace.spans:
            status = "error" if span.error else "ok"
            metrics.increment(
                "reactifact_agent_runs_total", agent=span.agent, status=status
            )
            metrics.observe(
                "reactifact_agent_latency_seconds",
                span.latency_ms / 1000.0,
                agent=span.agent,
            )
            for call in span.llm_calls:
                self._record_llm(call)
            self._record_artifacts(span)

    def _record_artifacts(self, span: AgentSpan) -> None:
        """Domain-adjacent counters straight from the span's reads/writes/edges.

        Mirrors whatever artifact types and relations the app uses (e.g. an
        `Answer`/`Evidence` pair and `supported_by`), with no app code.
        """
        metrics = self.metrics
        for ref in span.writes:
            metrics.increment(
                "reactifact_artifacts_written_total",
                op=ref.op_type or "write",
                type=ref.data_type or "unknown",
            )
        for ref in span.reads:
            metrics.increment(
                "reactifact_artifacts_read_total", type=ref.data_type or "unknown"
            )
        for relation in span.relations:
            metrics.increment(
                "reactifact_relations_total", relation=relation.relation or "unknown"
            )

    def _record_llm(self, call: LLMCall) -> None:
        metrics = self.metrics
        metrics.increment(
            "reactifact_llm_calls_total",
            provider=call.provider,
            model=call.model,
            agent=call.agent,
        )
        if call.error:
            metrics.increment(
                "reactifact_llm_errors_total", provider=call.provider, model=call.model
            )
        metrics.increment(
            "reactifact_llm_tokens_total",
            kind="prompt",
            provider=call.provider,
            model=call.model,
            value=float(call.prompt_tokens),
        )
        metrics.increment(
            "reactifact_llm_tokens_total",
            kind="completion",
            provider=call.provider,
            model=call.model,
            value=float(call.completion_tokens),
        )
        metrics.observe(
            "reactifact_llm_latency_seconds",
            call.latency_ms / 1000.0,
            provider=call.provider,
            model=call.model,
        )
        if self._pricer is not None:
            cost = cost_of(
                self._pricer, call.model, call.prompt_tokens, call.completion_tokens
            )
            metrics.increment(
                "reactifact_llm_cost_total",
                value=cost,
                provider=call.provider,
                model=call.model,
            )


def create_metrics_router(metrics: Metrics, *, path: str = "/metrics") -> Any:
    """A FastAPI router serving `metrics.render()` at `path` (needs the `web` extra)."""
    from fastapi import APIRouter
    from fastapi.responses import PlainTextResponse

    router = APIRouter()

    @router.get(path, include_in_schema=False)
    async def _metrics() -> PlainTextResponse:
        return PlainTextResponse(
            metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return router


__all__ = [
    "DEFAULT_BUCKETS",
    "Metrics",
    "MetricsTracer",
    "create_metrics_router",
]
