"""Per-run request context: `Runtime.arun(request=...)` -> `call.request`."""

import asyncio

from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    Runtime,
    create_agent,
    current_request,
    produce,
)


class In(BaseModel):
    text: str


class Out(BaseModel):
    text: str


def test_call_request_is_available_inside_produces():
    seen: dict[str, object] = {}

    @produce(Out)
    async def p(call):
        seen["user"] = call.request.get("user")
        seen["all"] = dict(call.request)
        call.effects.create(Out(text="ok"))
        return None

    agent = create_agent("a", consumes=[Consume(In)], produces=[p])
    ctx = Context()
    runtime = Runtime(ctx, agents=[agent])
    ctx.create(In(text="hi"))
    asyncio.run(runtime.arun(request={"user": "bob", "tenant": "acme"}))

    assert seen["user"] == "bob"
    assert seen["all"] == {"user": "bob", "tenant": "acme"}


def test_request_is_empty_when_not_passed_and_reset_after():
    seen: dict[str, object] = {}

    @produce(Out)
    async def p(call):
        seen["request"] = dict(call.request)
        call.effects.create(Out(text="ok"))
        return None

    agent = create_agent("a", consumes=[Consume(In)], produces=[p])
    ctx = Context()
    runtime = Runtime(ctx, agents=[agent])
    ctx.create(In(text="hi"))
    asyncio.run(runtime.arun())

    assert seen["request"] == {}
    # after the turn the var is restored — not leaked into the caller's context
    assert dict(current_request()) == {}


def test_chat_invoke_passes_request_to_produces(tmp_path):
    from reactifact import SessionStore
    from reactifact.chat import ChatAssistant
    from reactifact.checkpoints import FileKVBackend

    seen: dict[str, object] = {}

    @produce(Out)
    async def p(call):
        seen["user"] = call.request.get("user")
        call.effects.create(Out(text="ok"))
        return None

    agent = create_agent("a", consumes=[Consume(In)], produces=[p])
    assistant = ChatAssistant(
        store=SessionStore(FileKVBackend(str(tmp_path / "sessions"))),
        agents=[agent],
        user_message=In,
        reply=lambda ctx, mid: {"reply": "ok"},
    )
    result = asyncio.run(
        assistant.invoke("hi", session_id="s1", request={"user": "bob"})
    )
    assert result["reply"] == "ok"
    assert seen["user"] == "bob"
