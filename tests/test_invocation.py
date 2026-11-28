"""Generic invocations, idempotent chat turns and the chat `Turn` recipe."""

from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Artifact,
    Chat,
    Consume,
    Context,
    FakeLLM,
    Invocation,
    Pipeline,
    PipelineRunner,
    RunConfig,
    RuntimeResources,
    Step,
    current_invocation,
)
from reactifact.chat import ChatAssistant
from reactifact.checkpoints import (
    FileKVBackend,
    InMemoryKVBackend,
    SQLiteKVBackend,
)
from reactifact.recipes.conversation import Turn
from reactifact.session import SessionStore


class Q(BaseModel):
    text: str
    session_id: str = ""


class A(BaseModel):
    query_id: str
    text: str


class Answerer(Step[A]):
    reads = (Q,)

    async def run(self, question: Artifact[Q]) -> A:
        return A(query_id=question.id, text=f"answer:{question.data.text}")


def test_invocation_is_generic_metadata():
    inv = Invocation.new(
        idempotency_key="request-1", scope="tenant", metadata={"k": "v"}
    )
    assert inv.id
    assert inv.idempotency_key == "request-1"
    assert inv.scope == "tenant"
    assert dict(inv.metadata) == {"k": "v"}


def test_invocation_is_injected_into_steps_and_results():
    seen: list[str] = []

    class Remember(Step[A]):
        reads = (Q,)

        async def run(self, question: Q, invocation: Invocation) -> A:
            seen.append(invocation.id)
            return A(query_id=invocation.id, text=question.text)

    pipeline = Pipeline(
        agents=[Agent("remember", consumes=[Consume(Q)], steps=[Remember()])],
        seed_type=Q,
        answer_type=A,
    )
    inv = Invocation.new()
    result = PipelineRunner(
        pipeline, config=RunConfig(resources=RuntimeResources())
    ).run(Q(text="hello"), invocation=inv)

    assert seen == [inv.id]
    assert result.invocation is inv
    assert current_invocation() is None  # the run scope was reset


def test_turn_is_a_chat_invocation():
    turn = Turn.new("session-1", idempotency_key="turn-1", metadata={"ui": "web"})
    assert turn.id == turn.invocation.id
    assert turn.session_id == "session-1"
    assert turn.invocation.idempotency_key == "turn-1"
    assert turn.invocation.scope == "session-1"


def _assistant() -> tuple[ChatAssistant, SessionStore]:
    store = SessionStore(InMemoryKVBackend())

    def reply(context: Context, msg_id: str) -> dict:
        answers = [
            artifact.data
            for artifact in context.list_artifacts(A)
            if artifact.data.query_id == msg_id
        ]
        return {"reply": answers[-1].text if answers else "missing", "waiting": False}

    assistant = ChatAssistant(
        store=store,
        agents=[
            Agent(
                "answerer",
                consumes=[Consume(Q)],
                steps=[Answerer()],
            )
        ],
        user_message=Q,
        reply=reply,
        fallback_reply="degraded",
    )
    return assistant, store


def test_idempotent_invocation_replays_the_first_turn():
    assistant, store = _assistant()
    inv = Invocation.new(idempotency_key="chat-1")

    first = asyncio.run(assistant.invoke("hello", session_id="s1", invocation=inv))
    second = asyncio.run(assistant.invoke("hello", session_id="s1", invocation=inv))

    assert first["reply"] == "answer:hello"
    assert second["reply"] == "answer:hello"
    session = asyncio.run(store.open("s1"))
    assert len(session.context.list_artifacts(Q)) == 1
    assert len(session.context.list_artifacts(A)) == 1


def test_failed_invocation_can_be_retried():
    assistant, store = _assistant()
    inv = Invocation.new(idempotency_key="chat-fail")
    asyncio.run(
        store.put_invocation(
            "s2:chat-fail",
            {"status": "failed", "invocation_id": inv.id},
        )
    )

    payload = asyncio.run(assistant.invoke("hello", session_id="s2", invocation=inv))

    assert payload["reply"] == "answer:hello"
    record = asyncio.run(store.get_invocation("s2:chat-fail"))
    assert record is not None
    assert record["status"] == "done"
    assert record["message_id"]


def test_running_invocation_is_not_run_twice():
    assistant, store = _assistant()
    inv = Invocation.new(idempotency_key="chat-busy")
    asyncio.run(
        store.put_invocation(
            "s3:chat-busy",
            {"status": "running", "invocation_id": inv.id, "started_at": time.time()},
        )
    )

    with pytest.raises(Exception, match="already running"):
        asyncio.run(assistant.invoke("hello", session_id="s3", invocation=inv))


def test_invocation_records_are_not_listed_as_sessions():
    store = SessionStore(InMemoryKVBackend())

    async def scenario() -> list[str]:
        await store.put_invocation("k", {"status": "done"})
        return await store.list_sessions()

    assert asyncio.run(scenario()) == []


def test_store_claim_is_single_owner():
    store = SessionStore(InMemoryKVBackend())

    async def scenario():
        first = await store.claim_invocation("k", {"status": "running"})
        second = await store.claim_invocation("k", {"status": "running"})
        return first, second

    first, second = asyncio.run(scenario())
    assert first == (True, None)
    assert second[0] is False
    assert second[1] is not None
    assert second[1]["status"] == "running"
    assert isinstance(second[1]["started_at"], float)


def test_chat_passes_the_invocation_into_steps():
    seen: list[str] = []

    class Remember(Step[A]):
        reads = (Q,)

        async def run(self, question: Artifact[Q], invocation: Invocation) -> A:
            seen.append(invocation.id)
            return A(query_id=question.id, text=question.data.text)

    store = SessionStore(InMemoryKVBackend())
    assistant = ChatAssistant(
        store=store,
        agents=[Agent("remember", consumes=[Consume(Q)], steps=[Remember()])],
        user_message=Q,
        reply=lambda context, msg_id: {
            "reply": context.latest(A).data.text,
            "waiting": False,
        },
    )
    inv = Invocation.new(idempotency_key="chat-see")

    asyncio.run(assistant.invoke("hello", session_id="s4", invocation=inv))

    assert seen == [inv.id]


def test_invocation_claims_are_loop_safe():
    class SlowKV(InMemoryKVBackend):
        async def get(self, key):
            await asyncio.sleep(0)
            return await super().get(key)

        async def set(self, key, data):
            await asyncio.sleep(0)
            await super().set(key, data)

    store = SessionStore(SlowKV())

    async def contend(prefix: str):
        return await asyncio.gather(
            *(store.claim_invocation(prefix, {"index": index}) for index in range(4))
        )

    first = asyncio.run(contend("a"))
    second = asyncio.run(contend("b"))

    assert sum(1 for claimed, _ in first if claimed) == 1
    assert sum(1 for claimed, _ in second if claimed) == 1


def test_chat_rejects_llm_with_a_resources_factory():
    with pytest.raises(TypeError, match="per-turn resources= factory"):
        Chat(
            agents=[Agent("answerer", consumes=[Consume(Q)], steps=[Answerer()])],
            seed_type=Q,
            answer_type=A,
            store=SessionStore(InMemoryKVBackend()),
            resources=lambda: RuntimeResources(),
            config=RunConfig(llm=FakeLLM()),
        )


def test_chat_assistant_rejects_llm_with_a_resources_factory():
    with pytest.raises(TypeError, match="per-turn resources= factory"):
        ChatAssistant(
            agents=[Agent("answerer", consumes=[Consume(Q)], steps=[Answerer()])],
            user_message=Q,
            reply=lambda context, msg_id: {"reply": "x"},
            store=SessionStore(InMemoryKVBackend()),
            resources=lambda: RuntimeResources(),
            config=RunConfig(llm=FakeLLM()),
        )


def test_idempotency_keys_are_scoped_per_session():
    assistant, store = _assistant()
    INV = Invocation.new(idempotency_key="same-key")

    first = asyncio.run(assistant.invoke("hello", session_id="a", invocation=INV))
    second = asyncio.run(assistant.invoke("hello", session_id="b", invocation=INV))

    assert first["reply"] == "answer:hello"
    assert second["reply"] == "answer:hello"
    for session_id in ("a", "b"):
        context = asyncio.run(store.open(session_id)).context
        assert len(context.list_artifacts(Q)) == 1


def test_stale_running_invocation_is_reclaimable():
    store = SessionStore(InMemoryKVBackend(), invocation_stale_after=0.01)

    async def scenario():
        first, _ = await store.claim_invocation("stale", {"status": "running"})
        await asyncio.sleep(0.02)
        second, _ = await store.claim_invocation("stale", {"status": "running"})
        return first, second

    assert asyncio.run(scenario()) == (True, True)


def test_fresh_running_invocation_is_not_reclaimed():
    store = SessionStore(InMemoryKVBackend(), invocation_stale_after=60)

    async def scenario():
        first, _ = await store.claim_invocation("fresh", {"status": "running"})
        second, existing = await store.claim_invocation("fresh", {"status": "running"})
        return first, second, existing

    first, second, existing = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert existing is not None and existing["status"] == "running"


def test_set_if_absent_is_atomic_across_backends(tmp_path):
    async def scenario(backend):
        assert await backend.set_if_absent("k", {"v": 1}) is True
        assert await backend.set_if_absent("k", {"v": 2}) is False
        assert await backend.get("k") == {"v": 1}
        await backend.aclose()

    for backend in (
        InMemoryKVBackend(),
        FileKVBackend(str(tmp_path / "files")),
        SQLiteKVBackend(str(tmp_path / "kv.db")),
    ):
        asyncio.run(scenario(backend))


def test_fenced_completion_does_not_overwrite_a_reclaimed_claim():
    store = SessionStore(InMemoryKVBackend(), invocation_stale_after=0.0)

    async def scenario():
        first, _ = await store.claim_invocation(
            "k", {"status": "running", "claim_id": "old"}
        )
        second, _ = await store.claim_invocation(
            "k", {"status": "running", "claim_id": "new"}
        )
        written = await store.finish_invocation(
            "k", {"status": "done", "claim_id": "old"}, claim_id="old"
        )
        return first, second, written, await store.get_invocation("k")

    first, second, written, current = asyncio.run(scenario())
    assert (first, second) == (True, True)
    assert written is False
    assert current is not None
    assert current["claim_id"] == "new"
    assert current["status"] == "running"


def test_invocation_is_recorded_on_the_run_trace():
    class Sink:
        def __init__(self) -> None:
            self.traces = []

        async def export(self, trace) -> None:
            self.traces.append(trace)

    from reactifact.tracing import Tracer

    sink = Sink()
    pipeline = Pipeline(
        agents=[Agent("answerer", consumes=[Consume(Q)], steps=[Answerer()])],
        seed_type=Q,
        answer_type=A,
    )
    inv = Invocation.new(
        idempotency_key="trace-key", scope="tenant-1", metadata={"ui": "web"}
    )
    PipelineRunner(pipeline, config=RunConfig(tracer=Tracer(sinks=[sink]))).run(
        Q(text="hello"), invocation=inv
    )

    trace = sink.traces[-1]
    assert trace.invocation_id == inv.id
    assert trace.invocation_scope == "tenant-1"
    assert trace.idempotency_key == "trace-key"


def test_sqlite_set_if_absent_is_atomic_across_connections(tmp_path):
    path = str(tmp_path / "kv.db")

    async def scenario():
        left = SQLiteKVBackend(path)
        right = SQLiteKVBackend(path)
        results = await asyncio.gather(
            left.set_if_absent("k", {"from": "left"}),
            right.set_if_absent("k", {"from": "right"}),
        )
        value = await left.get("k")
        await left.aclose()
        await right.aclose()
        return results, value

    results, value = asyncio.run(scenario())
    assert sorted(results) == [False, True]
    assert value in ({"from": "left"}, {"from": "right"})


def test_trace_store_roundtrips_invocation_fields(tmp_path):
    from reactifact.tracing import Tracer, TraceStore

    store = TraceStore(str(tmp_path / "traces.db"))
    pipeline = Pipeline(
        agents=[Agent("answerer", consumes=[Consume(Q)], steps=[Answerer()])],
        seed_type=Q,
        answer_type=A,
    )
    inv = Invocation.new(idempotency_key="persisted-key", scope="tenant-2")
    result = PipelineRunner(
        pipeline,
        config=RunConfig(tracer=Tracer(store=store)),
    ).run(Q(text="hello"), invocation=inv)

    trace = asyncio.run(store.get(result.trace_id))
    store.close()

    assert trace is not None
    assert trace.invocation_id == inv.id
    assert trace.invocation_scope == "tenant-2"
    assert trace.idempotency_key == "persisted-key"


def test_invocation_metrics_count_claims_and_replays():
    from reactifact.metrics import Metrics, MetricsInvocationObserver

    metrics = Metrics()
    store = SessionStore(
        InMemoryKVBackend(),
        observer=MetricsInvocationObserver(metrics),
    )

    def reply(context, msg_id):
        return {"reply": "ok", "waiting": False}

    assistant = ChatAssistant(
        store=store,
        agents=[Agent("answerer", consumes=[Consume(Q)], steps=[Answerer()])],
        user_message=Q,
        reply=reply,
    )
    inv = Invocation.new(idempotency_key="metrics-key")

    asyncio.run(assistant.invoke("hello", session_id="m1", invocation=inv))
    asyncio.run(assistant.invoke("hello", session_id="m1", invocation=inv))

    rendered = metrics.render()
    assert 'reactifact_invocations_total{event="claimed"} 1' in rendered
    assert 'reactifact_invocations_total{event="replayed"} 1' in rendered
    assert 'reactifact_invocations_total{event="completed"} 1' in rendered
