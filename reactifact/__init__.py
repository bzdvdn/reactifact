"""reactifact's core public API.

Deliberately small: the primitives from the README's "Core primitives"
section, plus the everyday building blocks (tool calling, sessions, the LLM
provider protocol) most agents need regardless of what else they use.

Everything else — eval, tracing, checkpoint/branch backends beyond the
in-memory default, chat hooks beyond `Chat` (`ChatReply`, `ChatMemory`,
`ChatAssistant`), tool loops (`ToolUse`), provider specs, recipes, the
adaptive scheduler, replay, structured-LLM helpers, viz, prompts — is one
level down, in its own submodule (`reactifact.eval`, `reactifact.tracing`,
`reactifact.chat`, ...). Import it from there:

    from reactifact.structured import structured_llm
    from reactifact.tracing import TraceStore
    from reactifact.chat import ChatAssistant

This keeps `dir(reactifact)` / editor autocomplete to what you need to build a
first agent, and keeps optional-dependency features (Postgres, FastAPI) out
of the names you see by default even though they were always cheap to import
(the driver itself is still lazily imported inside the class, see
`reactifact._extras`).
"""

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _distribution_version

from .agent import (
    Agent,
    Drop,
    Edit,
    Emit,
    Ensure,
    Guard,
    GuardResult,
    Latest,
    Relate,
    Scope,
    Step,
    StepInvocation,
    Unscoped,
    Wakes,
    When,
)
from .agent.step import step
from .artifacts import Artifact, ArtifactConflict, ArtifactKey
from .branching import MergeConflict
from .budget import Budget, BudgetTracker, RunOutcome, RunStats
from .chat import Chat
from .commit import ContextCheckpoint
from .consume import Consume, IdempotentConsume
from .context import Context, ProvenanceLink, View
from .daemon import Daemon
from .decisions import Decision
from .di import Depends, Inject
from .effects import Effects, Handle
from .events import Event, EventType
from .interaction import Human, Outbox, Progress
from .interrupt import PendingAction, PendingQuestion
from .invocation import Invocation, current_invocation
from .logging import bind, configure_logging, get_logger
from .patches import Create, Delete, Link, Patch, Relation, Unlink, Update
from .pipeline import (
    Pipeline,
    PipelineDefinition,
    PipelineRunner,
    ReactiveSchedule,
    RunConfig,
    Schedule,
    Stage,
    StagedPipeline,
    StagedSchedule,
)
from .policy import RetryPolicy
from .provenance import (
    CALCULATED_FROM,
    CONTRADICTED_BY,
    DERIVED_FROM,
    EXTRACTED_FROM,
    MATERIALIZED_FROM,
    REFUTED_BY,
    RESOLVED_FROM,
    SUPPORTED_BY,
    RelationName,
)
from .providers import (
    EmbeddingProvider,
    FakeEmbedder,
    FakeLLM,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    Message,
)
from .read import Read
from .request import CurrentRequest, current_request
from .resources import ResourceKey, ResourceScope, RuntimeResources
from .runtime import RunResult, Runtime
from .session import Session, SessionStore
from .subflow import SubflowResult, run_subflow
from .tools import FunctionTool, Tool, ToolError, ToolOutput, tool
from .triggers import Trigger
from .types import register as register_type
from .validate import (
    ValidationError,
    ValidationIssue,
    ValidationReport,
    validate_contracts,
)

try:
    __version__ = _distribution_version("reactifact")
except _PackageNotFoundError:  # source checkout without an installed distribution
    __version__ = "0.0.0"

__all__ = [
    "Unscoped",
    "Artifact",
    "ArtifactKey",
    "ArtifactConflict",
    "Budget",
    "Chat",
    "BudgetTracker",
    "Consume",
    "Context",
    "IdempotentConsume",
    "ProvenanceLink",
    "ContextCheckpoint",
    "Create",
    "Daemon",
    "Decision",
    "Delete",
    "Depends",
    "EmbeddingProvider",
    "Effects",
    "Emit",
    "Ensure",
    "Guard",
    "GuardResult",
    "Relate",
    "Edit",
    "Drop",
    "Event",
    "EventType",
    "FakeEmbedder",
    "FakeLLM",
    "FunctionTool",
    "Handle",
    "Human",
    "Latest",
    "Link",
    "Message",
    "MergeConflict",
    "Outbox",
    "Inject",
    "Invocation",
    "current_invocation",
    "Patch",
    "Agent",
    "Read",
    "Pipeline",
    "PipelineDefinition",
    "PipelineRunner",
    "RunConfig",
    "Schedule",
    "Stage",
    "StagedPipeline",
    "StagedSchedule",
    "ReactiveSchedule",
    "PendingAction",
    "PendingQuestion",
    "Progress",
    "Step",
    "StepInvocation",
    "SubflowResult",
    "Relation",
    "RelationName",
    "CurrentRequest",
    "ResourceKey",
    "ResourceScope",
    "RetryPolicy",
    "SUPPORTED_BY",
    "RESOLVED_FROM",
    "REFUTED_BY",
    "MATERIALIZED_FROM",
    "EXTRACTED_FROM",
    "DERIVED_FROM",
    "CONTRADICTED_BY",
    "CALCULATED_FROM",
    "RunOutcome",
    "RunResult",
    "RunStats",
    "Scope",
    "Runtime",
    "RuntimeResources",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseChunk",
    "Session",
    "SessionStore",
    "Tool",
    "ToolError",
    "ToolOutput",
    "Trigger",
    "Unlink",
    "Update",
    "ValidationError",
    "ValidationIssue",
    "ValidationReport",
    "View",
    "Wakes",
    "When",
    "bind",
    "configure_logging",
    "current_request",
    "get_logger",
    "register_type",
    "run_subflow",
    "step",
    "tool",
    "validate_contracts",
]
