import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from reactifact import Consume, SessionStore, create_agent, produce
from reactifact.chat import ChatAssistant
from reactifact.checkpoints import FileKVBackend
from reactifact.web import create_chat_router


class Q(BaseModel):
    text: str
    session_id: str = ""


class A(BaseModel):
    text: str


async def make_answer(call):
    call.effects.create(A(text="answer"))
    return None


ASSISTANT_PROD = produce(A)(make_answer)
ANSWER_AGENT = create_agent("a", consumes=[Consume(Q)], produces=[ASSISTANT_PROD])


def make_assistant(tmp_path: str) -> ChatAssistant:
    store = SessionStore(FileKVBackend(os.path.join(tmp_path, "sess")))

    def reply(ctx, msg_id):
        a = ctx.latest(A)
        return {"reply": a.data.text if a else "fallback", "waiting": False}

    return ChatAssistant(
        store=store,
        agents=[ANSWER_AGENT],
        user_message=Q,
        reply=reply,
        max_concurrency=1,
    )


def test_stream_events(tmp_path):
    assistant = make_assistant(str(tmp_path))

    async def run():
        events = [ev async for ev in assistant.stream("hi", session_id="s1")]
        return events

    events = asyncio.run(run())
    assert [e.kind for e in events] == ["session", "message"]
    assert events[1].payload["reply"] == "answer"


def test_history_roundtrip(tmp_path):
    assistant = make_assistant(str(tmp_path))
    _drain(assistant.stream("hi", session_id="s1"))
    history = asyncio.run(assistant.history("s1"))
    roles = [m["role"] for m in history["messages"]]
    assert roles == ["user", "assistant"]
    assert history["messages"][0]["text"] == "hi"
    assert history["messages"][1]["text"] == "answer"


def test_history_empty_for_unknown_session(tmp_path):
    assistant = make_assistant(str(tmp_path))
    assert asyncio.run(assistant.history("nope"))["messages"] == []


def _drain(stream):
    async def _collect():
        return [ev async for ev in stream]

    return asyncio.run(_collect())


def test_web_router_contract(tmp_path):
    assistant = make_assistant(str(tmp_path))
    app = FastAPI()
    app.include_router(create_chat_router(assistant))
    client = TestClient(app)

    assert client.get("/api/health").json() == {"ok": True}

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hi", "session_id": "s1"},
    ) as response:
        body = "".join(response.iter_text())

    assert "event: session" in body
    assert "event: message" in body
    assert '"reply": "answer"' in body

    runs = client.get("/api/runs/s1").json()
    assert any(m["role"] == "assistant" for m in runs["messages"])
    assert client.delete("/api/runs/s1").json() == {"ok": True}
    assert client.get("/api/runs/s1").json()["messages"] == []


def test_chat_event_kind_is_a_closed_literal(tmp_path):
    import pytest
    from pydantic import ValidationError
    from reactifact.chat import ChatEvent

    assert ChatEvent(kind="status").kind == "status"
    with pytest.raises(ValidationError):
        ChatEvent(kind="bogus")


def test_web_router_custom_vocabulary(tmp_path):
    """A client with its own wire format is configuration, not a fork."""
    assistant = make_assistant(str(tmp_path))
    app = FastAPI()
    app.include_router(
        create_chat_router(
            assistant,
            event_names={"message": "content", "session": "start"},
            forward_kinds=("message",),  # drop session/status entirely
            done_event="done",
            payload_shaper=lambda ev: {"text": (ev.payload or {}).get("reply", "")},
        )
    )
    client = TestClient(app)

    with client.stream(
        "POST", "/api/chat/stream", json={"message": "hi", "session_id": "s1"}
    ) as response:
        body = "".join(response.iter_text())

    assert "event: start" not in body
    assert "event: session" not in body
    assert "event: content" in body
    assert "event: done" in body
    assert '"text": "answer"' in body
    assert '"reply"' not in body  # payload was reshaped


def test_web_router_openapi_publishes_event_schema(tmp_path):
    assistant = make_assistant(str(tmp_path))
    app = FastAPI()
    app.include_router(
        create_chat_router(assistant, event_names={"message": "content"})
    )

    responses = app.openapi()["paths"]["/api/chat/stream"]["post"]["responses"]
    assert "200" in responses
    schema = responses["200"]["content"]["text/event-stream"]["schema"]
    # the frame schema (ChatEvent) is published, enum kinds and all
    assert "session" in str(schema) and "status" in str(schema)
    assert "content" in responses["200"]["description"]


def test_disconnect_cancels_the_running_turn(tmp_path):
    """A dropped SSE client must cancel in-flight work, not leave it burning.

    Mirrors Starlette: when the streaming task is cancelled, the `CancelledError`
    unwinds through `ChatAssistant.stream` → `run_message` → `Runtime.astream`,
    whose `finally` cancels the runner task — so a slow agent/LLM call stops.
    """
    import contextlib

    from reactifact import Agent, Produce

    state = {"started": False, "cancelled": False}

    class Slow(Produce[A]):
        async def produce(self, call):
            state["started"] = True
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

    class SlowAgent(Agent):
        name = "slow"
        consumes = [Consume(Q)]
        produces = [Slow()]

    store = SessionStore(FileKVBackend(os.path.join(str(tmp_path), "sess")))
    assistant = ChatAssistant(
        store=store,
        agents=[SlowAgent()],
        user_message=Q,
        reply=lambda ctx, mid: {"reply": "done", "waiting": False},
        max_concurrency=1,
    )

    async def scenario():
        agen = assistant.stream("hi", session_id="s1")
        assert (await agen.__anext__()).kind == "session"
        pending = asyncio.ensure_future(agen.__anext__())  # drive into the turn
        for _ in range(200):
            if state["started"]:
                break
            await asyncio.sleep(0.005)
        assert state["started"], "the agent never started"
        pending.cancel()  # what a client disconnect looks like to the server
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await pending
        await agen.aclose()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert state["cancelled"], "the in-flight turn was not cancelled"


def test_web_extra_error_is_readable(tmp_path, monkeypatch):
    """Missing fastapi → a readable install hint, not a bare ModuleNotFoundError."""
    import builtins
    import sys

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return real_import(name, *args, **kwargs)

    # Drop cached fastapi so importlib actually tries to import it again.
    for mod in [m for m in sys.modules if m == "fastapi" or m.startswith("fastapi.")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(builtins, "__import__", blocked)
    from reactifact import Consume, create_agent, produce
    from reactifact.chat import ChatAssistant

    class _Q(BaseModel):
        text: str
        session_id: str = ""

    async def _m(call):
        return None

    agent = create_agent("a", consumes=[Consume(_Q)], produces=[produce(_Q)(_m)])
    with pytest.raises(ImportError, match=r"reactifact\[web\]"):
        create_chat_router(
            ChatAssistant(
                store=None,
                agents=[agent],
                user_message=_Q,
                reply=lambda ctx, mid: {},
            )
        )


def test_runtime_crash_degrades_to_fallback_message(tmp_path, caplog):
    """A crashing produce must not escape as an exception from stream()."""

    from reactifact import Agent, Produce

    class Boom(Produce[A]):
        async def produce(self, call):
            raise RuntimeError("boom inside the agent")

    class BoomAgent(Agent):
        name = "boom"
        consumes = [Consume(Q)]
        produces = [Boom()]

    store = SessionStore(FileKVBackend(os.path.join(str(tmp_path), "sess")))
    assistant = ChatAssistant(
        store=store,
        agents=[BoomAgent()],
        user_message=Q,
        reply=lambda ctx, mid: {"reply": "should not reach", "waiting": False},
        max_concurrency=1,
        fallback_reply="degraded",
    )

    events = _drain(assistant.stream("hi", session_id="s1"))

    kinds = [ev.kind for ev in events]
    assert kinds[-1] == "message"
    assert events[-1].payload["reply"] == "degraded"
    assert events[-1].payload.get("error") is True
    assert any("runtime crashed" in r.message for r in caplog.records)


def test_reply_hook_crash_degrades_to_fallback(tmp_path, caplog):
    """A crashing reply hook must degrade to the fallback, not a 500."""

    def boom_reply(ctx, mid):
        raise RuntimeError("reply hook exploded")

    store = SessionStore(FileKVBackend(os.path.join(str(tmp_path), "sess")))
    assistant = ChatAssistant(
        store=store,
        agents=[ANSWER_AGENT],
        user_message=Q,
        reply=boom_reply,
        max_concurrency=1,
        fallback_reply="degraded",
    )

    events = _drain(assistant.stream("hi", session_id="s2"))

    assert events[-1].kind == "message"
    assert events[-1].payload["reply"] == "degraded"
    assert events[-1].payload.get("error") is True


class _SpyLLM:
    """Minimal LLMProvider stand-in that tracks whether aclose() ran."""

    def __init__(self) -> None:
        self.closed = False

    async def complete(self, request):
        from reactifact.providers import LLMResponse

        return LLMResponse(text="ok")

    async def stream(self, request):
        from reactifact.providers import LLMResponseChunk

        yield LLMResponseChunk(text="ok")

    async def aclose(self) -> None:
        self.closed = True


def test_callable_resources_closed_after_each_turn(tmp_path):
    """resources= a callable is assumed turn-scoped: a fresh RuntimeResources
    is built per turn, and its provider must be closed after that turn."""
    from reactifact import RuntimeResources

    built: list[_SpyLLM] = []

    def fresh_resources():
        llm = _SpyLLM()
        built.append(llm)
        return RuntimeResources(llm=llm)

    store = SessionStore(FileKVBackend(os.path.join(str(tmp_path), "sess")))
    assistant = ChatAssistant(
        store=store,
        agents=[ANSWER_AGENT],
        user_message=Q,
        reply=lambda ctx, mid: {"reply": "ok", "waiting": False},
        resources=fresh_resources,
        max_concurrency=1,
    )

    _drain(assistant.stream("hi", session_id="s4"))
    _drain(assistant.stream("hi again", session_id="s4"))

    assert len(built) == 2  # a fresh RuntimeResources was built each turn
    assert all(llm.closed for llm in built)


def test_shared_resources_instance_not_closed(tmp_path):
    """A plain (non-callable) resources= instance must survive the turn —
    it's shared across future turns/sessions, not closed automatically."""
    from reactifact import RuntimeResources

    llm = _SpyLLM()
    shared = RuntimeResources(llm=llm)

    store = SessionStore(FileKVBackend(os.path.join(str(tmp_path), "sess")))
    assistant = ChatAssistant(
        store=store,
        agents=[ANSWER_AGENT],
        user_message=Q,
        reply=lambda ctx, mid: {"reply": "ok", "waiting": False},
        resources=shared,
        max_concurrency=1,
    )

    _drain(assistant.stream("hi", session_id="s5"))
    assert llm.closed is False


def test_session_open_crash_degrades_to_fallback(tmp_path, caplog):
    """A failing session open must degrade to a fallback message, not an exception."""

    class BoomStore:
        def open(self, session_id, resources=None):
            raise RuntimeError("backend down")

        def delete_session(self, session_id):
            pass

    assistant = ChatAssistant(
        store=BoomStore(),
        agents=[ANSWER_AGENT],
        user_message=Q,
        reply=lambda ctx, mid: {"reply": "never", "waiting": False},
        max_concurrency=1,
        fallback_reply="degraded",
    )

    events = _drain(assistant.stream("hi", session_id="s3"))

    assert events[-1].kind == "message"
    assert events[-1].payload["reply"] == "degraded"
