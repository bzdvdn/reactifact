"""Shared OTLP/HTTP JSON payload-building for tracing sinks.

Both `LangfuseTracer` and `OTLPTracer` build the same wire format
(https://opentelemetry.io/docs/specs/otlp/) by hand over plain `httpx`,
without depending on `opentelemetry-sdk` — Langfuse only accepts OTLP/HTTP
now (see `langfuse.py`'s docstring), and `OTLPTracer` targets any other
OTLP/HTTP collector the same way. What's shared is purely mechanical: id
generation and the attribute-value encoding. Span *shape* (what attributes,
what namespace) stays sink-specific, since Langfuse's `langfuse.observation.*`
keys and `OTLPTracer`'s vendor-neutral `gen_ai.*`/`reactifact.*` keys mean
different things to different backends.

Trace/span ids are deterministic (sha256 of reactifact's own `RunTrace.id`)
rather than randomly generated or W3C-propagated — a sink fires once per
already-finished run, so there's no live trace context to propagate into.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any


def unix_nanos(value: datetime) -> str:
    return str(int(value.timestamp() * 1_000_000_000))


def trace_id(seed: str) -> str:
    return hashlib.sha256(f"trace:{seed}".encode()).hexdigest()[:32]


def span_id(seed: str) -> str:
    return hashlib.sha256(f"span:{seed}".encode()).hexdigest()[:16]


def attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        otlp_value: dict[str, Any] = {"boolValue": value}
    elif isinstance(value, int):
        otlp_value = {"intValue": str(value)}
    elif isinstance(value, float):
        otlp_value = {"doubleValue": value}
    else:
        if value is None:
            value = ""
        elif not isinstance(value, str):
            value = json.dumps(value, default=str)
        otlp_value = {"stringValue": value}
    return {"key": key, "value": otlp_value}


def type_summary(refs: list[Any]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for ref in refs:
        kind = getattr(ref, "data_type", None) or type(ref).__name__
        summary[kind] = summary.get(kind, 0) + 1
    return summary
