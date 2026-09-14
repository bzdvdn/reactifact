"""Observability: traces of runs (§54).

Package: models (`models`), delivery (`tracer`), storage (`store`), UI
(`web` + `templates/traces.html`). The public API is re-exported here —
but without `web`, so that importing the package does not pull in FastAPI.
"""

from .langfuse import LangfuseTracer
from .models import AgentSpan, ArtifactRef, LLMCall, RelationRef, RunTrace
from .otlp import OTLPTracer
from .postgres import PostgresStore
from .store import TraceReader, TraceSink, TraceStore
from .tracer import CompositeTracer, RecordingLLM, RunTracer, Tracer

__all__ = [
    "AgentSpan",
    "ArtifactRef",
    "CompositeTracer",
    "LLMCall",
    "LangfuseTracer",
    "OTLPTracer",
    "PostgresStore",
    "RecordingLLM",
    "RelationRef",
    "RunTrace",
    "RunTracer",
    "TraceReader",
    "TraceSink",
    "TraceStore",
    "Tracer",
]
