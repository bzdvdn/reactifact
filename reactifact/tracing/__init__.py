"""Observability: traces of runs (§54).

Package: models (`models`), delivery (`tracer`), storage (`store`), UI
(`web` + `templates/traces.html`). The public API is re-exported here —
but without `web`, so that importing the package does not pull in FastAPI.
"""

from ..tools import ToolRun
from .columns import TraceColumn
from .langfuse import LangfuseTracer
from .models import (
    AgentContract,
    AgentSpan,
    ArtifactRef,
    LLMCall,
    ProduceRun,
    RelationRef,
    RunTrace,
    StepRun,
    Tag,
    TagAssignment,
    TraceLimits,
)
from .otlp import OTLPTracer
from .postgres import PostgresStore
from .store import (
    TraceAnnotator,
    TraceReader,
    TraceSink,
    TraceStore,
    TraceStoreProtocol,
)
from .tracer import CompositeTracer, RecordingLLM, RunTracer, Tracer

__all__ = [
    "AgentContract",
    "AgentSpan",
    "ArtifactRef",
    "CompositeTracer",
    "LLMCall",
    "LangfuseTracer",
    "OTLPTracer",
    "PostgresStore",
    "ProduceRun",
    "RecordingLLM",
    "RelationRef",
    "RunTrace",
    "RunTracer",
    "StepRun",
    "Tag",
    "TagAssignment",
    "TraceAnnotator",
    "TraceColumn",
    "TraceLimits",
    "TraceReader",
    "TraceSink",
    "TraceStore",
    "TraceStoreProtocol",
    "ToolRun",
    "Tracer",
]
