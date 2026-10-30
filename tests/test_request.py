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


class Query(BaseModel):
    text: str
    session_id: str = ""
    category: str = ""


def _query_assistant(tmp_path, create_message):
    from reactifact import SessionStore
    from reactifact.chat import ChatAssistant
    from reactifact.checkpoints import FileKVBackend

    store = SessionStore(FileKVBackend(str(tmp_path / "sessions")))
    assistant = ChatAssistant(
        store=store,
        agents=[],
        user_message=Query,
        reply=lambda ctx, mid: {"reply": "ok"},
        create_message=create_message,
    )
    return assistant, store


def test_create_message_reads_request_through_context(tmp_path):
    seen: dict[str, object] = {}

    def create_message(ctx, text):
        seen["request"] = dict(ctx.request)
        return ctx.create(Query(text=text, category=ctx.request.get("category", ""))).id

    assistant, store = _query_assistant(tmp_path, create_message)
    result = asyncio.run(
        assistant.invoke("hi", session_id="s1", request={"category": "helm"})
    )

    assert result["reply"] == "ok"
    assert seen["request"] == {"category": "helm"}
    ctx = asyncio.run(store.open("s1")).context
    assert ctx.list_artifacts(Query)[0].data.category == "helm"
    # the request is not leaked past the turn
    assert dict(current_request()) == {}


def test_create_message_four_arg_shape_receives_request(tmp_path):
    seen: dict[str, object] = {}

    def create_message(ctx, text, session_id, request):
        seen["session_id"] = session_id
        seen["request"] = dict(request or {})
        return ctx.create(
            Query(
                text=text,
                session_id=session_id,
                category=(request or {}).get("category", ""),
            )
        ).id

    assistant, _ = _query_assistant(tmp_path, create_message)
    asyncio.run(assistant.invoke("hi", session_id="s2", request={"category": "argocd"}))

    assert seen == {"session_id": "s2", "request": {"category": "argocd"}}
