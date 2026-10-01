"""`ChatAssistant(dispatcher=...)` — outbound side effects from a chat turn."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import PendingAction
from reactifact.agents import create_agent
from reactifact.chat import ChatAssistant
from reactifact.checkpoints import InMemoryKVBackend
from reactifact.consume import Consume
from reactifact.context import Context
from reactifact.produce import Produce, ProduceCall
from reactifact.session import SessionStore


class _Msg(BaseModel):
    text: str


class _Reply(BaseModel):
    text: str


class _Notifier(Produce[_Reply]):
    artifact_type = _Reply
    also_creates = (PendingAction,)

    async def produce(self, call: ProduceCall) -> None:
        msg = call.trigger
        if msg is None:
            return
        call.effects.act(
            "notify", key=f"notify:{msg.data.text}", payload={"text": msg.data.text}
        )
        call.effects.create(_Reply(text=f"noted: {msg.data.text}"), id="reply:1")


def _reply(ctx: Context, _message_id: str) -> dict[str, str]:
    replies = ctx.list_artifacts(_Reply)
    return {"reply": replies[-1].data.text if replies else ""}


def test_chat_assistant_runs_the_outbox_dispatcher():
    sent: list[str] = []

    async def dispatcher(context: Context, action) -> None:
        sent.append(action.data.idempotency_key)

    store = SessionStore(InMemoryKVBackend())
    assistant = ChatAssistant(
        store=store,
        agents=[
            create_agent("notify", consumes=[Consume(_Msg)], produces=[_Notifier()])
        ],
        user_message=_Msg,
        reply=_reply,
        dispatcher=dispatcher,
    )

    result = asyncio.run(assistant.invoke("hi", session_id="s1"))

    assert result["reply"] == "noted: hi"
    assert sent == ["notify:hi"]

    session = asyncio.run(store.open("s1"))
    action = session.context.get("action:notify:hi")
    assert action is not None
    assert action.data.status == "dispatched"
