"""A2A (Agent2Agent) client and server (§ interop)."""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    Produce,
    Runtime,
    RuntimeResources,
    create_agent,
)
from reactifact.a2a import (
    A2AAgentTool,
    A2AClient,
    A2AError,
    A2ARemoteProduce,
    Message,
    Task,
    TaskStatus,
    a2a_tool,
    create_a2a_router,
    remote_agent,
)
from reactifact.a2a.models import JSONRPCRequest, UserMessage
from reactifact.interrupt import PendingQuestion


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Echo(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call):
        call.effects.create(Answer(text=call.trigger.data.text.upper()))


class Slow(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call):
        await asyncio.sleep(30)
        call.effects.create(Answer(text="slow"))


class AskOrAnswer(Produce[Answer]):
    """First a PendingQuestion (input-required), then an Answer once resumed."""

    artifact_type = Answer
    also_creates = (PendingQuestion,)

    async def produce(self, call):
        data = call.trigger.data
        if isinstance(data, PendingQuestion):
            if data.answered:
                call.effects.create(Answer(text=f"resolved: {data.resolution}"))
            return
        call.effects.ask("What value?", kind="clarify")


def _echo_agent():
    return create_agent("echo", consumes=[Consume(UserMessage)], produces=[Echo()])


def _app(agents):
    app = FastAPI()
    app.include_router(create_a2a_router(agents, name="echo-app"))
    return app


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


def test_message_helpers():
    message = Message.user("hello world")
    assert message.role == "user"
    assert message.text == "hello world"
    assert Message.agent("hi").role == "agent"


def test_task_model_round_trip():
    task = Task(
        id="t1",
        contextId="c1",
        status=TaskStatus(state="completed", message=Message.agent("done")),
    )
    assert Task.model_validate(task.model_dump(mode="json")).status.state == "completed"


# --------------------------------------------------------------------------- #
# Server (FastAPI)
# --------------------------------------------------------------------------- #


def _rpc(client: TestClient, method: str, params: dict) -> dict:
    response = client.post(
        "/",
        json=JSONRPCRequest(id="1", method=method, params=params).model_dump(
            mode="json"
        ),
    )
    assert response.status_code == 200
    return response.json()


def test_agent_card_is_served_on_both_paths():
    client = TestClient(_app([_echo_agent()]))
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
        card = client.get(path).json()
        assert card["name"] == "echo-app"
        assert card["capabilities"]["streaming"] is True
        assert [skill["id"] for skill in card["skills"]] == ["echo"]


def test_message_send_completes():
    client = TestClient(_app([_echo_agent()]))
    body = _rpc(
        client,
        "message/send",
        {"message": Message.user("hi").model_dump(mode="json")},
    )
    task = body["result"]
    assert task["status"]["state"] == "completed"
    assert task["artifacts"][0]["parts"][0]["text"] == "HI"


def test_tasks_get_and_cancel():
    client = TestClient(_app([_echo_agent()]))
    task_id = _rpc(
        client, "message/send", {"message": Message.user("hi").model_dump(mode="json")}
    )["result"]["id"]

    fetched = _rpc(client, "tasks/get", {"id": task_id})
    assert fetched["result"]["id"] == task_id

    missing = _rpc(client, "tasks/get", {"id": "nope"})
    assert missing["error"]["code"] == -32001

    canceled = _rpc(client, "tasks/cancel", {"id": task_id})
    # already completed (terminal): state is unchanged.
    assert canceled["result"]["status"]["state"] == "completed"


def test_unknown_method_errors():
    client = TestClient(_app([_echo_agent()]))
    body = _rpc(client, "does/not/exist", {})
    assert body["error"]["code"] == -32601


def test_server_task_store_is_bounded():
    """A long-lived server must not grow its in-process task store without
    bound: the oldest records are evicted once the cap is reached."""
    from reactifact.a2a.server import (
        _MAX_TASKS,
        _A2AServer,
        default_create_message,
        default_reply,
    )

    server = _A2AServer(
        [_echo_agent()],
        context_factory=Context,
        create_message=default_create_message,
        reply=default_reply,
        budget=None,
        max_iterations=100,
    )
    for _ in range(_MAX_TASKS + 20):
        server._record_for(Message.user("hi"))
    assert len(server._tasks) <= _MAX_TASKS


def test_server_stream_cancels_run_when_the_client_disconnects():
    """A dropped SSE client must cancel the in-flight run — otherwise it keeps
    executing and is destroyed pending at loop shutdown ("Task was destroyed
    but it is pending!")."""
    from reactifact.a2a.models import MessageSendParams
    from reactifact.a2a.server import _A2AServer, default_create_message, default_reply

    slow = create_agent("slow", consumes=[Consume(UserMessage)], produces=[Slow()])
    server = _A2AServer(
        [slow],
        context_factory=Context,
        create_message=default_create_message,
        reply=default_reply,
        budget=None,
        max_iterations=100,
    )
    params = MessageSendParams(message=Message.user("hi"))

    async def run():
        agen = server.stream("1", params)
        assert (await agen.__anext__()).startswith("data:")  # the working frame
        puller = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0)  # enter the wait: the run task is in flight
        puller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await puller
        await agen.aclose()
        for _ in range(10):
            await asyncio.sleep(0)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(run()) == []


def test_hitl_input_required_then_resume():
    agent = create_agent(
        "asker",
        consumes=[Consume(UserMessage), Consume(PendingQuestion)],
        produces=[AskOrAnswer()],
    )
    client = TestClient(_app([agent]))
    first = _rpc(
        client,
        "message/send",
        {"message": Message.user("please help").model_dump(mode="json")},
    )["result"]
    assert first["status"]["state"] == "input-required"
    assert first["status"]["message"]["parts"][0]["text"] == "What value?"

    resumed = _rpc(
        client,
        "message/send",
        {
            "message": Message.user(
                "42", taskId=first["id"], contextId=first["contextId"]
            ).model_dump(mode="json")
        },
    )["result"]
    assert resumed["id"] == first["id"]
    assert resumed["status"]["state"] == "completed"
    assert resumed["artifacts"][0]["parts"][0]["text"] == "resolved: 42"


def test_message_stream_emits_sse():
    client = TestClient(_app([_echo_agent()]))
    response = client.post(
        "/",
        json=JSONRPCRequest(
            id="1",
            method="message/stream",
            params={"message": Message.user("hi").model_dump(mode="json")},
        ).model_dump(mode="json"),
    )
    assert response.status_code == 200
    body = response.text
    assert "data:" in body
    assert '"state":"working"' in body
    assert '"state":"completed"' in body


# --------------------------------------------------------------------------- #
# Client (against the server over ASGI)
# --------------------------------------------------------------------------- #


def _client_for(app: FastAPI) -> tuple[A2AClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    return A2AClient("http://test/", client=http), http


def test_client_fetches_card_and_sends():
    app = _app([_echo_agent()])
    a2a, http = _client_for(app)

    async def run():
        try:
            card = await a2a.fetch_agent_card()
            assert card.name == "echo-app"
            result = await a2a.send("hello")
            assert isinstance(result, Task)
            assert result.status.state == "completed"
            assert result.artifacts[0].parts[0].text == "HELLO"
            assert (await a2a.get_task(result.id)).id == result.id
        finally:
            await http.aclose()

    asyncio.run(run())


def test_client_streams_updates():
    app = _app([_echo_agent()])
    a2a, http = _client_for(app)

    async def run():
        try:
            seen = [event async for event in a2a.stream("hi")]
            states = [event.status.state for event in seen if isinstance(event, Task)]
            assert "working" in states
            assert "completed" in states
        finally:
            await http.aclose()

    asyncio.run(run())


def test_agent_tool_delegates_to_remote_agent():
    app = _app([_echo_agent()])
    a2a, http = _client_for(app)
    tool = A2AAgentTool(a2a)

    async def run():
        try:
            output = await tool.execute({"message": "hey"})
            assert output.text == "HEY"
            assert output.error == ""
        finally:
            await http.aclose()

    asyncio.run(run())


def test_client_raises_on_jsonrpc_error():
    app = _app([_echo_agent()])
    a2a, http = _client_for(app)

    async def run():
        try:
            with pytest.raises(A2AError):
                await a2a.get_task("missing")
        finally:
            await http.aclose()

    asyncio.run(run())


def test_a2a_tool_factory():
    tool = a2a_tool("http://example.test/", name="remote")
    assert isinstance(tool, A2AAgentTool)
    assert tool.name == "remote"
    assert tool.schema["required"] == ["message"]


# --------------------------------------------------------------------------- #
# Remote agent as a scheduled node (A2ARemoteProduce / remote_agent)
# --------------------------------------------------------------------------- #


def test_remote_agent_runs_as_a_local_node():
    app = _app([_echo_agent()])
    a2a, http = _client_for(app)
    node = remote_agent("remote", a2a, consumes=[Consume(Question)], output_type=Answer)

    async def run():
        try:
            ctx = Context(resources=RuntimeResources())
            ctx.create(Question(text="hello"))
            await Runtime(ctx, agents=[node]).arun()
            answer = ctx.latest(Answer)
            assert answer is not None
            assert answer.data.text == "HELLO"
        finally:
            await http.aclose()

    asyncio.run(run())


def test_remote_agent_maps_input_required_to_hitl():
    asker = create_agent(
        "asker",
        consumes=[Consume(UserMessage), Consume(PendingQuestion)],
        produces=[AskOrAnswer()],
    )
    app = _app([asker])
    a2a, http = _client_for(app)
    node = remote_agent("remote", a2a, consumes=[Consume(Question)], output_type=Answer)

    async def run():
        try:
            ctx = Context(resources=RuntimeResources())
            ctx.create(Question(text="please help"))
            runtime = Runtime(ctx, agents=[node])
            await runtime.arun()

            questions = list(ctx.pending_questions())
            assert len(questions) == 1
            assert questions[0].data.question == "What value?"
            assert questions[0].data.notes["a2a_agent"] == "remote"

            ctx.resume(questions[0].id, "42")
            await runtime.arun()
            answer = ctx.latest(Answer)
            assert answer is not None
            assert answer.data.text == "resolved: 42"
        finally:
            await http.aclose()

    asyncio.run(run())


def test_remote_produce_is_exported_with_pending_question_widening():
    produce = A2ARemoteProduce("http://example.test/", output_type=Answer, name="x")
    assert produce.artifact_type is Answer
    assert PendingQuestion in produce.also_creates
