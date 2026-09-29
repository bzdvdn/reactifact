"""A2A wire models (Agent2Agent protocol, JSON-RPC binding, v0.3 shape).

Pydantic mirrors of the A2A data model — `AgentCard`, `Message`/`Part`,
`Task`/`TaskStatus`, `Artifact` — plus the JSON-RPC 2.0 envelopes and error
codes. No dependency beyond pydantic (core); these are plain, serializable
models so both the client and the server speak one shape.

Field names follow the JSON wire form (camelCase, `kind` discriminators), so
`model_dump()`/`model_validate()` are the JSON-RPC payloads directly. The
protocol version implemented is `0.3`; the newer proto/REST bindings rename
fields (`supportedInterfaces`, `TASK_STATE_*`) — out of scope here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["user", "agent"]
PartKind = Literal["text", "file", "data"]
TaskState = Literal[
    "submitted",
    "working",
    "input-required",
    "completed",
    "canceled",
    "failed",
    "rejected",
    "auth-required",
    "unknown",
]

#: Terminal A2A task states — a stream/run is done once it reaches one.
TERMINAL_STATES: frozenset[str] = frozenset(
    {"completed", "canceled", "failed", "rejected"}
)
#: Interrupted states — the run paused for the caller (HITL).
INTERRUPTED_STATES: frozenset[str] = frozenset({"input-required", "auth-required"})


class Part(BaseModel):
    """One content unit of a `Message`/`Artifact` (text, file, or data)."""

    kind: PartKind = "text"
    text: str | None = None
    data: Any | None = None
    url: str | None = None
    raw: str | None = None  # base64 in JSON
    mediaType: str | None = None
    filename: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def text_part(cls, text: str) -> Part:
        return cls(kind="text", text=text)

    @classmethod
    def data_part(cls, data: Any) -> Part:
        return cls(kind="data", data=data, mediaType="application/json")

    def as_text(self) -> str:
        """Best-effort text of this part (JSON for a data part, else '')."""
        if self.kind == "text":
            return self.text or ""
        if self.kind == "data":
            import json

            return json.dumps(self.data, ensure_ascii=False, default=str)
        return self.text or ""


class Message(BaseModel):
    """A communication turn: a role plus one or more `Part`s."""

    kind: Literal["message"] = "message"
    messageId: str
    role: Role
    parts: list[Part] = Field(default_factory=list)
    contextId: str | None = None
    taskId: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    referenceTaskIds: list[str] = Field(default_factory=list)

    @classmethod
    def user(cls, text: str, *, message_id: str = "", **kwargs: Any) -> Message:
        from uuid import uuid4

        return cls(
            messageId=message_id or str(uuid4()),
            role="user",
            parts=[Part.text_part(text)],
            **kwargs,
        )

    @classmethod
    def agent(cls, text: str, *, message_id: str = "", **kwargs: Any) -> Message:
        from uuid import uuid4

        return cls(
            messageId=message_id or str(uuid4()),
            role="agent",
            parts=[Part.text_part(text)],
            **kwargs,
        )

    @property
    def text(self) -> str:
        """Concatenated text of the message's parts."""
        return "\n".join(part.as_text() for part in self.parts if part.as_text())


class Artifact(BaseModel):
    """A concrete deliverable produced by the agent during a task."""

    artifactId: str
    name: str = ""
    description: str = ""
    parts: list[Part] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskStatus(BaseModel):
    state: TaskState = "submitted"
    message: Message | None = None
    timestamp: str | None = None


class Task(BaseModel):
    """A stateful unit of work; the A2A analogue of one reactifact run."""

    kind: Literal["task"] = "task"
    id: str
    contextId: str
    status: TaskStatus = Field(default_factory=TaskStatus)
    artifacts: list[Artifact] = Field(default_factory=list)
    history: list[Message] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    examples: list[str] | None = None
    inputModes: list[str] | None = None
    outputModes: list[str] | None = None


class AgentCapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = False


class AgentProvider(BaseModel):
    organization: str = ""
    url: str = ""


class AgentCard(BaseModel):
    """The discovery document, served at the well-known path."""

    protocolVersion: str = "0.3.0"
    name: str
    description: str = ""
    url: str = ""
    version: str = "0.1.0"
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    defaultInputModes: list[str] = Field(default_factory=lambda: ["text/plain"])
    defaultOutputModes: list[str] = Field(default_factory=lambda: ["text/plain"])
    skills: list[AgentSkill] = Field(default_factory=list)
    provider: AgentProvider | None = None
    documentationUrl: str | None = None


# --------------------------------------------------------------------------- #
# JSON-RPC 2.0 envelopes and A2A error codes
# --------------------------------------------------------------------------- #


class ErrorCode:
    """A2A JSON-RPC error codes (spec §6.11 / §8.4)."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    TASK_NOT_FOUND = -32001
    TASK_NOT_CANCELABLE = -32002
    PUSH_NOTIFICATION_NOT_SUPPORTED = -32003
    UNSUPPORTED_OPERATION = -32004


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Any | None = None


class JSONRPCRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JSONRPCResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    result: Any | None = None
    error: JSONRPCError | None = None

    @classmethod
    def ok(cls, request_id: str | int | None, result: Any) -> JSONRPCResponse:
        return cls(id=request_id, result=result)

    @classmethod
    def fail(
        cls, request_id: str | int | None, code: int, message: str, data: Any = None
    ) -> JSONRPCResponse:
        return cls(
            id=request_id, error=JSONRPCError(code=code, message=message, data=data)
        )


class MessageSendParams(BaseModel):
    """`message/send` / `message/stream` params."""

    message: Message
    configuration: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskQueryParams(BaseModel):
    id: str
    historyLength: int | None = None


class TaskIdParams(BaseModel):
    id: str


def reply_message(text: str, *, task_id: str, context_id: str) -> Message:
    """Builds an agent reply `Message` tied to a task/context."""
    return Message.agent(text, taskId=task_id, contextId=context_id)


class UserMessage(BaseModel):
    """Default seed artifact for the A2A server (an app usually passes its own).

    The server's default `create_message` hook creates one of these; a real app
    consuming its own artifact types (a `Question`, a `UserMsg`, …) supplies a
    `create_message` that creates *that* type instead.
    """

    text: str
    query_id: str = ""


def artifact_from_text(
    text: str, *, artifact_id: str, name: str = "result"
) -> Artifact:
    return Artifact(artifactId=artifact_id, name=name, parts=[Part.text_part(text)])


__all__ = [
    "INTERRUPTED_STATES",
    "TERMINAL_STATES",
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
    "PartKind",
    "Role",
    "Task",
    "TaskIdParams",
    "TaskQueryParams",
    "TaskState",
    "TaskStatus",
    "UserMessage",
    "artifact_from_text",
    "reply_message",
]
