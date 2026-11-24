"""reactifact's core public API.

Deliberately small: the primitives from the README's "Core primitives"
section, plus the everyday building blocks (tool calling, sessions, the LLM
provider protocol) most agents need regardless of what else they use.

Everything else — eval, tracing, checkpoint/branch backends beyond the
in-memory default, the chat/web layer, the adaptive scheduler, replay,
structured-LLM helpers, viz, prompts — is one level down, in its own
submodule (`reactifact.eval`, `reactifact.tracing`, `reactifact.chat`, ...). Import it
from there:

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

from .agents import Agent, create_agent
from .artifacts import Artifact, ArtifactConflict
from .branching import MergeConflict
from .budget import Budget, BudgetTracker, RunOutcome, RunStats
from .commit import ContextCheckpoint
from .consume import Consume, consume
from .context import Context, ProvenanceLink, View
from .effects import Effects, Handle
from .events import Event, EventType
from .interrupt import PendingAction, PendingQuestion
from .logging import bind, configure_logging, get_logger
from .patches import Create, Delete, Link, Patch, Relation, Unlink, Update
from .policy import RetryPolicy

# Deprecated 0.1.x aliases: importable for the compatibility window, but kept
# out of `__all__` (the public surface) until their removal.
from .produce import Produce, ProduceCall, produce
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
from .request import current_request
from .resources import ResourceKey, ResourceScope, RuntimeResources
from .runtime import Runtime
from .session import Session, SessionStore
from .step import (
    Latest,
    Scope,
    Step,
    StepCall,
    StepGroup,
    Wakes,
    When,
    create_step_group,
    step,
)
from .subflow import SubflowResult, run_subflow
from .tools import FunctionTool, Tool, ToolError, ToolOutput, tool
from .triggers import Trigger
from .types import register as register_type
from .validate import (
    ValidationError,
    ValidationIssue,
    ValidationReport,
    validate_agents,
    validate_contracts,
)

try:
    __version__ = _distribution_version("reactifact")
except _PackageNotFoundError:  # source checkout without an installed distribution
    __version__ = "0.0.0"

__all__ = [
    "Agent",
    "StepGroup",
    "Artifact",
    "ArtifactConflict",
    "Budget",
    "BudgetTracker",
    "Consume",
    "Context",
    "ProvenanceLink",
    "ContextCheckpoint",
    "Create",
    "Delete",
    "EmbeddingProvider",
    "Effects",
    "Event",
    "EventType",
    "FakeEmbedder",
    "FakeLLM",
    "FunctionTool",
    "Handle",
    "Latest",
    "Link",
    "Message",
    "MergeConflict",
    "Patch",
    "PendingAction",
    "PendingQuestion",
    "Step",
    "StepCall",
    "SubflowResult",
    "Relation",
    "ResourceKey",
    "ResourceScope",
    "RetryPolicy",
    "RunOutcome",
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
    "consume",
    "create_agent",
    "create_step_group",
    "current_request",
    "get_logger",
    "register_type",
    "run_subflow",
    "step",
    "tool",
    "validate_agents",
    "validate_contracts",
]
