"""Observability: traces of runs (§54).

Package: models (`models`), delivery (`tracer`), storage (`store`), UI
(`web` + `templates/traces.html`). The public API is re-exported here —
but without `web`, so that importing the package does not pull in FastAPI.
"""

from ..tools import ToolRun
from .columns import TraceColumn
from .diff import TraceDiff, diff_traces
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
from .models import (
    StepGroupSpan as StepGroupSpan,
)
from .otlp import OTLPTracer
from .postgres import PostgresStore
from .retention import RetentionPolicy
from .sampling import SamplingPolicy, keep_trace
from .store import (
    TraceAnnotator,
    TraceReader,
    TraceSink,
    TraceStore,
    TraceStoreProtocol,
)
from .tracer import CompositeTracer, RecordingLLM, RunTracer, Tracer

# `StepGroupSpan` stays importable for 0.x code but is not advertised in `__all__`.

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
    "RetentionPolicy",
    "SamplingPolicy",
    "TraceDiff",
    "TraceLimits",
    "TraceReader",
    "TraceSink",
    "TraceStore",
    "diff_traces",
    "keep_trace",
    "TraceStoreProtocol",
    "ToolRun",
    "Tracer",
]
