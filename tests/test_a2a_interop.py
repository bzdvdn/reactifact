"""Interop: the official `a2a-sdk` client against our A2A server.

Skipped when `a2a-sdk` is not installed (it is a dev dependency). This is the
guard that our hand-rolled JSON-RPC binding really speaks the wire shape the
reference client emits — a plain JSON-RPC client we also test against would not
prove that.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import BaseModel
from reactifact import Consume, Produce, create_agent
from reactifact.a2a import create_a2a_router
from reactifact.a2a.models import UserMessage

a2a_types = pytest.importorskip("a2a.types")
a2a_client = pytest.importorskip("a2a.client")

from fastapi import FastAPI  # noqa: E402  (after importorskip)


class Answer(BaseModel):
    text: str


class Echo(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call):
        call.effects.create(Answer(text=call.trigger.data.text.upper()))


def _app() -> FastAPI:
    agent = create_agent("echo", consumes=[Consume(UserMessage)], produces=[Echo()])
    app = FastAPI()
    app.include_router(create_a2a_router([agent], name="interop-echo"))
    return app


def test_official_client_resolves_our_agent_card():
    app = _app()

    async def run():
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        try:
            card = await a2a_client.A2ACardResolver(
                http, "http://test"
            ).get_agent_card()
            assert card.name == "interop-echo"
            assert card.capabilities.streaming is True
        finally:
            await http.aclose()

    asyncio.run(run())


def test_official_client_sends_message_and_gets_a_completed_task():
    app = _app()

    async def run():
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        try:
            client = await a2a_client.create_client(
                "http://test",
                a2a_client.ClientConfig(streaming=False, httpx_client=http),
            )
            message = a2a_types.Message(
                message_id="m1",
                role=a2a_types.Role.ROLE_USER,
                parts=[a2a_types.Part(text="hello")],
            )
            tasks = [
                event.task
                async for event in client.send_message(
                    a2a_types.SendMessageRequest(message=message)
                )
                if event.WhichOneof("payload") == "task"
            ]
            assert tasks, "no task in the stream response"
            task = tasks[-1]
            assert task.status.state == a2a_types.TaskState.TASK_STATE_COMPLETED
            assert task.artifacts[0].parts[0].text == "HELLO"
        finally:
            await http.aclose()

    asyncio.run(run())
