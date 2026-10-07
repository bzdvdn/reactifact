"""A2A server: expose reactifact agents as an Agent2Agent endpoint.

`create_a2a_router(agents, ...)` returns a FastAPI `APIRouter` that serves:

- `GET /.well-known/agent-card.json` (and the legacy `/agent.json`) — the Agent
  Card, built from the agents (`skills` come from each agent's `produces`);
- `POST {prefix}/` — the JSON-RPC endpoint: `message/send`, `message/stream`,
  `tasks/get`, `tasks/cancel`.

The protocol is the JSON-RPC binding (v0.3 shape) over `httpx`/FastAPI — no
extra dependency (the server needs the existing `web` extra). Mapping to
reactifact:

- a `Task` is one conversation; a new `message/send` seeds the app's input via
  `create_message(ctx, text)` and runs the agents to a fixpoint;
- a paused HITL run (`PendingQuestion`) becomes `input-required`, and the next
  `message/send` for the same `taskId` resumes it;
- the reply is read with `reply(ctx, seed_id)` (default: the last artifact with
  a `text` field) and returned as a task `Artifact` + status `Message`;
- `message/stream` emits the initial working `Task`, then a `status-update` per
  `context.announce()` progress event, then the final task.

Tasks are kept in-process (like `reactifact.chat`); an app that needs durable
tasks persists the `Context` with a `Session` itself.
"""

import asyncio
import contextlib
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ..agents import Agent
from ..budget import Budget
from ..context import Context
from ..resources import RuntimeResources
from ..runtime import Runtime
from ..streaming import ProgressEvent
from .models import (
    TERMINAL_STATES,
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    ErrorCode,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    MessageSendParams,
    Task,
    TaskIdParams,
    TaskQueryParams,
    TaskStatus,
    UserMessage,
    artifact_from_text,
    reply_message,
)

CreateMessage = Callable[[Context, str], str]
Reply = Callable[[Context, str], str]
ContextFactory = Callable[[], Context]

#: In-process task store bound. A long-lived A2A server must not grow `_tasks`
#: without limit; the oldest completed task is evicted first (see `_evict`).
_MAX_TASKS = 512


def default_create_message(context: Context, text: str) -> str:
    """Default input hook: seed a `UserMessage` artifact (apps override this)."""
    return context.create(UserMessage(text=text)).id


def default_reply(context: Context, seed_id: str) -> str:
    """Default reply hook: the last artifact with a non-empty `text` field."""
    for artifact in reversed(context.list_artifacts()):
        text = getattr(artifact.data, "text", None)
        if isinstance(text, str) and text:
            return text
    return ""


@dataclass
class _TaskRecord:
    id: str
    context_id: str
    context: Context
    seed_id: str = ""
    pending_id: str = ""
    state: str = "submitted"
    history: list[Message] = field(default_factory=list)
    artifacts: list[Any] = field(default_factory=list)
    status_message: Message | None = None


class _A2AServer:
    """The protocol logic behind the router: task store + the reactifact bridge."""

    def __init__(
        self,
        agents: Sequence[Agent],
        *,
        context_factory: ContextFactory,
        create_message: CreateMessage,
        reply: Reply,
        budget: Budget | None,
        max_iterations: int,
    ) -> None:
        self.agents = list(agents)
        self._context_factory = context_factory
        self._create_message = create_message
        self._reply = reply
        self._budget = budget
        self._max_iterations = max_iterations
        self._tasks: OrderedDict[str, _TaskRecord] = OrderedDict()

    def _record_for(self, message: Message) -> _TaskRecord:
        if message.taskId and message.taskId in self._tasks:
            self._tasks.move_to_end(message.taskId)
            return self._tasks[message.taskId]
        record = _TaskRecord(
            id=message.taskId or uuid4().hex,
            context_id=message.contextId or uuid4().hex,
            context=self._context_factory(),
        )
        self._tasks[record.id] = record
        self._evict()
        return record

    def _evict(self) -> None:
        """Keep `_tasks` bounded: drop the oldest terminal task first."""
        if len(self._tasks) <= _MAX_TASKS:
            return
        for task_id, record in list(self._tasks.items()):
            if record.state in TERMINAL_STATES:
                del self._tasks[task_id]
                break
        else:
            self._tasks.popitem(last=False)

    async def _execute(
        self,
        record: _TaskRecord,
        text: str,
        *,
        on_event: Callable[[ProgressEvent], None] | None = None,
    ) -> None:
        context = record.context
        if record.pending_id:
            context.resume(record.pending_id, text)
        else:
            record.seed_id = self._create_message(context, text)
        runtime = Runtime(context, agents=list(self.agents), budget=self._budget)
        if on_event is None:
            await runtime.arun(max_iterations=self._max_iterations)
        else:
            async for event in runtime.astream(max_iterations=self._max_iterations):
                on_event(event)

        pending = list(context.pending_questions())
        if pending:
            record.pending_id = pending[0].id
            record.state = "input-required"
            record.status_message = reply_message(
                pending[0].data.question,
                task_id=record.id,
                context_id=record.context_id,
            )
            return
        record.pending_id = ""
        record.state = "completed"
        reply_text = self._reply(context, record.seed_id)
        record.status_message = reply_message(
            reply_text, task_id=record.id, context_id=record.context_id
        )
        record.artifacts = [
            artifact_from_text(reply_text, artifact_id=f"{record.id}:artifact")
        ]
        record.history.append(record.status_message)

    def task(self, record: _TaskRecord) -> Task:
        return Task(
            id=record.id,
            contextId=record.context_id,
            status=TaskStatus(state=record.state, message=record.status_message),  # type: ignore[arg-type]
            artifacts=record.artifacts,
            history=record.history,
        )

    async def handle_send(self, params: MessageSendParams) -> Task:
        record = self._record_for(params.message)
        record.history.append(params.message)
        await self._execute(record, params.message.text)
        return self.task(record)

    async def stream(
        self, request_id: str | int | None, params: MessageSendParams
    ) -> AsyncIterator[str]:
        record = self._record_for(params.message)
        record.history.append(params.message)
        record.state = "working"
        yield _sse(request_id, self.task(record))
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()
        run = asyncio.create_task(
            self._execute(record, params.message.text, on_event=queue.put_nowait)
        )
        try:
            while not run.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.05)
                except TimeoutError:
                    continue
                yield _sse(request_id, _status_update(record, "working", event.message))
            await run
            yield _sse(request_id, self.task(record))
        finally:
            # A dropped SSE client closes this generator mid-run: cancel and
            # await the run, or it keeps executing (tokens!) and is destroyed
            # pending ("Task was destroyed but it is pending!").
            if not run.done():
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run

    def get(self, task_id: str) -> Task | None:
        record = self._tasks.get(task_id)
        return self.task(record) if record is not None else None

    def cancel(self, task_id: str) -> Task | None:
        record = self._tasks.get(task_id)
        if record is None:
            return None
        if record.state not in TERMINAL_STATES:
            record.state = "canceled"
            record.pending_id = ""
        return self.task(record)


def _sse(request_id: str | int | None, result: Any) -> str:
    payload = (
        result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    )
    envelope = JSONRPCResponse.ok(request_id, payload)
    return f"data: {envelope.model_dump_json()}\n\n"


def _status_update(record: _TaskRecord, state: str, text: str) -> dict[str, Any]:
    message = reply_message(text, task_id=record.id, context_id=record.context_id)
    return {
        "kind": "status-update",
        "taskId": record.id,
        "contextId": record.context_id,
        "final": state in TERMINAL_STATES,
        "status": {
            "state": state,
            "message": message.model_dump(mode="json"),
        },
    }


def _card(
    agents: Sequence[Agent], *, name: str, description: str, url: str, version: str
) -> AgentCard:
    skills: list[AgentSkill] = []
    for agent in agents:
        produces = agent.produces or []
        types = sorted(
            {p.artifact_type.__name__ for p in produces if p.artifact_type is not None}
        )
        identifier = agent.name or type(agent).__name__.lower()
        skills.append(
            AgentSkill(
                id=identifier,
                name=identifier,
                description=", ".join(types) or "agent",
                tags=types,
            )
        )
    return AgentCard(
        name=name,
        description=description,
        url=url,
        version=version,
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
    )


def create_a2a_router(
    agents: Sequence[Agent],
    *,
    name: str = "reactifact",
    description: str = "A reactifact agent, exposed over the Agent2Agent protocol.",
    url: str | None = None,
    version: str | None = None,
    prefix: str = "",
    context_factory: ContextFactory | None = None,
    create_message: CreateMessage = default_create_message,
    reply: Reply = default_reply,
    budget: Budget | None = None,
    max_iterations: int = 100,
) -> Any:
    """A FastAPI router exposing `agents` over A2A (needs the `web` extra).

    `url` is the JSON-RPC endpoint advertised in the Agent Card; by default it
    is derived from the request (the app's base URL + `prefix`). `create_message`
    and `reply` adapt the app's own artifact types (like `reactifact.chat`);
    without them the server seeds a `UserMessage` and replies with the last
    artifact that has a `text` field.
    """
    from fastapi import APIRouter, Request
    from fastapi.responses import StreamingResponse

    from .. import __version__

    server = _A2AServer(
        agents,
        context_factory=context_factory
        or (lambda: Context(resources=RuntimeResources())),
        create_message=create_message,
        reply=reply,
        budget=budget,
        max_iterations=max_iterations,
    )
    router = APIRouter(prefix=prefix)

    def build_card(request: Request) -> AgentCard:
        endpoint = url or str(request.base_url).rstrip("/") + prefix + "/"
        return _card(
            agents,
            name=name,
            description=description,
            url=endpoint,
            version=version or __version__,
        )

    @router.get("/.well-known/agent-card.json")
    async def agent_card(request: Request) -> Any:
        return build_card(request).model_dump(mode="json")

    @router.get("/.well-known/agent.json")
    async def legacy_agent_card(request: Request) -> Any:
        return build_card(request).model_dump(mode="json")

    async def dispatch(
        request_id: str | int | None, method: str, params: dict[str, Any]
    ) -> Any:
        if method == "message/send":
            sent = await server.handle_send(MessageSendParams.model_validate(params))
            return _ok(request_id, sent)
        if method == "tasks/get":
            found = server.get(TaskQueryParams.model_validate(params).id)
            if found is None:
                return _error(request_id, ErrorCode.TASK_NOT_FOUND, "task not found")
            return _ok(request_id, found)
        if method == "tasks/cancel":
            cancelled = server.cancel(TaskIdParams.model_validate(params).id)
            if cancelled is None:
                return _error(request_id, ErrorCode.TASK_NOT_FOUND, "task not found")
            return _ok(request_id, cancelled)
        return _error(
            request_id, ErrorCode.METHOD_NOT_FOUND, f"unknown method {method!r}"
        )

    @router.post("/")
    async def rpc(request: Request) -> Any:
        try:
            payload = await request.json()
            envelope = JSONRPCRequest.model_validate(payload)
        except Exception:
            return _error(None, ErrorCode.PARSE_ERROR, "invalid JSON-RPC request")
        if envelope.method == "message/stream":
            try:
                params = MessageSendParams.model_validate(envelope.params)
            except Exception as exc:
                return _error(envelope.id, ErrorCode.INVALID_PARAMS, str(exc))
            return StreamingResponse(
                server.stream(envelope.id, params), media_type="text/event-stream"
            )
        try:
            return await dispatch(envelope.id, envelope.method, envelope.params)
        except Exception as exc:
            return _error(envelope.id, ErrorCode.INVALID_PARAMS, str(exc))

    return router


def _ok(request_id: str | int | None, task: Task) -> Any:
    from fastapi.responses import JSONResponse

    envelope = JSONRPCResponse.ok(request_id, task.model_dump(mode="json"))
    return JSONResponse(envelope.model_dump(mode="json"))


def _error(request_id: str | int | None, code: int, message: str) -> Any:
    from fastapi.responses import JSONResponse

    envelope = JSONRPCResponse.fail(request_id, code, message)
    return JSONResponse(envelope.model_dump(mode="json"))


__all__ = [
    "ContextFactory",
    "CreateMessage",
    "Reply",
    "create_a2a_router",
    "default_create_message",
    "default_reply",
]
