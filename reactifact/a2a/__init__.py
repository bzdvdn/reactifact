"""A2A (Agent2Agent) integration: call remote agents, or serve reactifact ones.

No extra dependency: the client uses `httpx` (core), the server uses FastAPI
from the existing `web` extra and imports it lazily — so importing this package
never needs FastAPI.

- Client (`reactifact.a2a.client`): `A2AClient` (Agent Card + `message/send`,
  `message/stream`, `tasks/get`, `tasks/cancel`) and `A2AAgentTool`/`a2a_tool`,
  a reactifact `Tool` that delegates to a remote agent.
- Server (`reactifact.a2a.server`): `create_a2a_router(agents, ...)` — an Agent
  Card plus a JSON-RPC endpoint where a `Task` is a conversation and a HITL
  `PendingQuestion` maps to `input-required`.

The JSON-RPC binding implements the A2A `0.3` wire shape (camelCase fields,
`kind`-discriminated parts).
"""

from __future__ import annotations

from .client import (
    AGENT_CARD_PATH,
    LEGACY_AGENT_CARD_PATH,
    A2AAgentTool,
    A2AClient,
    A2AError,
    a2a_tool,
)
from .models import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Artifact,
    ErrorCode,
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    MessageSendParams,
    Part,
    Task,
    TaskState,
    TaskStatus,
    UserMessage,
)
from .remote import A2ARemoteProduce, remote_agent
from .server import create_a2a_router, default_create_message, default_reply

__all__ = [
    "AGENT_CARD_PATH",
    "LEGACY_AGENT_CARD_PATH",
    "A2AAgentTool",
    "A2AClient",
    "A2AError",
    "A2ARemoteProduce",
    "AgentCapabilities",
    "AgentCard",
    "AgentProvider",
    "AgentSkill",
    "Artifact",
    "ErrorCode",
    "JSONRPCError",
    "JSONRPCRequest",
    "JSONRPCResponse",
    "Message",
    "MessageSendParams",
    "Part",
    "Task",
    "TaskState",
    "TaskStatus",
    "UserMessage",
    "a2a_tool",
    "create_a2a_router",
    "default_create_message",
    "default_reply",
    "remote_agent",
]
