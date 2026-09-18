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

from .agents import Agent, create_agent
from .artifacts import Artifact
from .branching import MergeConflict
from .budget import Budget, RunOutcome, RunStats
from .consume import Consume, consume
from .context import Context, View
from .effects import Effects, Handle
from .events import Event, EventType
from .interrupt import PendingQuestion
from .patches import Create, Delete, Link, Patch, Relation, Unlink, Update
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
from .resources import RuntimeResources
from .runtime import Runtime
from .session import Session, SessionStore
from .tools import FunctionTool, Tool, ToolOutput, tool
from .triggers import Trigger

__version__ = "0.8.0"

__all__ = [
    "Agent",
    "Artifact",
    "Budget",
    "Consume",
    "Context",
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
    "Link",
    "Message",
    "MergeConflict",
    "Patch",
    "PendingQuestion",
    "Produce",
    "ProduceCall",
    "Relation",
    "RunOutcome",
    "RunStats",
    "Runtime",
    "RuntimeResources",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseChunk",
    "Session",
    "SessionStore",
    "Tool",
    "ToolOutput",
    "Trigger",
    "Unlink",
    "Update",
    "View",
    "consume",
    "create_agent",
    "produce",
    "tool",
]
