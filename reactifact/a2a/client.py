"""A2A client: call a remote Agent2Agent server, and expose one as a `Tool`.

Speaks the JSON-RPC binding (v0.3 shape) over `httpx` — no extra dependency.
Two ways to consume a remote agent:

- `A2AClient` — the low-level protocol surface (`fetch_agent_card`, `send`,
  `stream`, `get_task`, `cancel_task`), returning the A2A `Task`/`Message`.
- `A2AAgentTool` / `a2a_tool` — a reactifact `Tool` wrapping a remote agent, so
  an `LLMAgent` can delegate to it: `message/send`, then the reply's text (or
  the task's artifact) becomes the tool result.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .._httpx import LoopBoundClient
from ..tools import Tool, ToolOutput
from .models import (
    AgentCard,
    ErrorCode,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    MessageSendParams,
    Task,
    TaskIdParams,
    TaskQueryParams,
)

#: Well-known Agent Card paths (v0.3 recommends `agent-card.json`; v0.2 used
#: `agent.json` — the server serves both).
AGENT_CARD_PATH = "/.well-known/agent-card.json"
LEGACY_AGENT_CARD_PATH = "/.well-known/agent.json"


class A2AError(Exception):
    """A remote A2A server returned a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"A2A error {code}: {message}")


def _default_card_url(url: str) -> str:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    return origin + AGENT_CARD_PATH


def _as_message(
    value: str | Message, *, task_id: str | None, context_id: str | None
) -> Message:
    if isinstance(value, Message):
        message = value.model_copy()
        if task_id is not None:
            message.taskId = task_id
        if context_id is not None:
            message.contextId = context_id
        return message
    return Message.user(value, taskId=task_id, contextId=context_id)


def _parse_result(result: Any) -> Task | Message | dict[str, Any]:
    """Reads a `message/send`/`stream` result into its typed model."""
    if isinstance(result, dict):
        kind = result.get("kind")
        if kind == "task":
            return Task.model_validate(result)
        if kind == "message":
            return Message.model_validate(result)
        return result
    return {}


class A2AClient:
    """A JSON-RPC A2A client for one server `url`.

    `url` is the JSON-RPC endpoint from the server's Agent Card; the card is
    fetched from the well-known path on the same origin unless `card_url` is
    given. `headers` are sent on every request (auth lives here, per the spec:
    credentials travel in HTTP headers, not in A2A messages). Pass `client=` to
    inject an `httpx.AsyncClient` (tests, a custom transport).
    """

    def __init__(
        self,
        url: str,
        *,
        card_url: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        client: Any | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.card_url = card_url or _default_card_url(self.url)
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        self._http = LoopBoundClient(
            lambda: httpx.AsyncClient(timeout=timeout, headers=self._headers),
            client=client,
        )

    async def fetch_agent_card(self) -> AgentCard:
        response = await self._http.get().get(self.card_url)
        response.raise_for_status()
        return AgentCard.model_validate(response.json())

    async def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        payload = JSONRPCRequest(
            id=uuid4().hex, method=method, params=params
        ).model_dump(mode="json")
        response = await self._http.get().post(self.url, json=payload)
        response.raise_for_status()
        envelope = JSONRPCResponse.model_validate(response.json())
        if envelope.error is not None:
            raise A2AError(
                envelope.error.code, envelope.error.message, envelope.error.data
            )
        return envelope.result

    async def send(
        self,
        message: str | Message,
        *,
        task_id: str | None = None,
        context_id: str | None = None,
        blocking: bool = True,
    ) -> Task | Message:
        """`message/send` → the task state (or a direct reply `Message`)."""
        params = MessageSendParams(
            message=_as_message(message, task_id=task_id, context_id=context_id),
            configuration=None if blocking else {"blocking": False},
        )
        result = _parse_result(
            await self._rpc("message/send", params.model_dump(mode="json"))
        )
        if not isinstance(result, (Task, Message)):
            raise A2AError(ErrorCode.INTERNAL_ERROR, "unexpected message/send result")
        return result

    async def stream(
        self,
        message: str | Message,
        *,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> AsyncIterator[Task | Message | dict[str, Any]]:
        """`message/stream` → an async iterator of task/message/update events."""
        params = MessageSendParams(
            message=_as_message(message, task_id=task_id, context_id=context_id)
        )
        payload = JSONRPCRequest(
            id=uuid4().hex,
            method="message/stream",
            params=params.model_dump(mode="json"),
        ).model_dump(mode="json")
        client = self._http.get()
        async with client.stream("POST", self.url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if not data or data == "[DONE]":
                    continue
                envelope = JSONRPCResponse.model_validate_json(data)
                if envelope.error is not None:
                    raise A2AError(
                        envelope.error.code,
                        envelope.error.message,
                        envelope.error.data,
                    )
                yield _parse_result(envelope.result)

    async def get_task(
        self, task_id: str, *, history_length: int | None = None
    ) -> Task:
        params = TaskQueryParams(id=task_id, historyLength=history_length)
        return Task.model_validate(
            await self._rpc("tasks/get", params.model_dump(mode="json"))
        )

    async def cancel_task(self, task_id: str) -> Task:
        params = TaskIdParams(id=task_id)
        return Task.model_validate(
            await self._rpc("tasks/cancel", params.model_dump(mode="json"))
        )

    async def aclose(self) -> None:
        await self._http.aclose()


def _task_reply_text(task: Task) -> str:
    if task.status.message is not None and task.status.message.text:
        return task.status.message.text
    return "\n".join(
        part.as_text() for artifact in task.artifacts for part in artifact.parts
    )


class A2AAgentTool(Tool):
    """A reactifact `Tool` that delegates to a remote A2A agent.

    The tool takes a single `message` string, sends it with `message/send`, and
    returns the reply text (the task's status message, else its artifacts) — so
    an `LLMAgent` can call another agent the way it calls any tool.
    """

    def __init__(
        self,
        target: str | A2AClient,
        *,
        name: str = "a2a_agent",
        description: str | None = None,
        card_url: str | None = None,
        headers: dict[str, str] | None = None,
        client: Any | None = None,
    ) -> None:
        self.client = (
            target
            if isinstance(target, A2AClient)
            else A2AClient(target, card_url=card_url, headers=headers, client=client)
        )
        self.name = name
        self.description = description or (
            f"Send a request to the A2A agent at {self.client.url} and get its reply."
        )
        self.schema = {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The request to send to the remote agent.",
                }
            },
            "required": ["message"],
        }

    async def execute(self, args: dict[str, Any]) -> ToolOutput:
        text = str(args.get("message", "")).strip()
        if not text:
            return ToolOutput(error="'message' is required")
        try:
            result = await self.client.send(text)
        except A2AError as exc:
            return ToolOutput(error=str(exc))
        if isinstance(result, Task):
            return ToolOutput(text=_task_reply_text(result), data=result.model_dump())
        if isinstance(result, Message):
            return ToolOutput(text=result.text, data=result.model_dump())
        return ToolOutput(text="", data=result)


def a2a_tool(
    url: str,
    *,
    name: str = "a2a_agent",
    description: str | None = None,
    card_url: str | None = None,
    headers: dict[str, str] | None = None,
) -> A2AAgentTool:
    """Builds an `A2AAgentTool` for the server at `url` (convenience wrapper)."""
    return A2AAgentTool(
        url, name=name, description=description, card_url=card_url, headers=headers
    )


__all__ = [
    "AGENT_CARD_PATH",
    "LEGACY_AGENT_CARD_PATH",
    "A2AAgentTool",
    "A2AClient",
    "A2AError",
    "a2a_tool",
]
