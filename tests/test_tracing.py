import asyncio
import json

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Runtime, RuntimeResources
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse, Message
from reactifact.tracing import (
    AgentSpan,
    ArtifactRef,
    LLMCall,
    RecordingLLM,
    RelationRef,
    RunTrace,
    Tracer,
    TraceStore,
)


def run(coro):
    return asyncio.run(coro)


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class ReplyLLM(LLMProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text='{"text":"привет"}',
            usage={"prompt_tokens": 12, "completion_tokens": 7},
        )

    async def stream(self, request):
        yield LLMResponse(text="")


class Greeter(Agent):
    consumes = [Consume(Question)]

    async def run(self, event, context):
        q = context.get(event.artifact_id)
        if q is None:
            return None
        return Patch().create(Answer(text="Привет!"))


class CountingQuestion(Question):
    """Counts model_dump calls to verify memoization."""

    dumps: int = 0

    def model_dump(self, *args, **kwargs):
        type(self).dumps += 1
        return super().model_dump(*args, **kwargs)


class Note(BaseModel):
    text: str


class Linker(Agent):
    """Writes two artifacts and a provenance edge via the patch."""

    consumes = [Consume(Question)]

    async def run(self, event, context):
        return (
            Patch()
            .create(Note(text="one"), id="note:1")
            .create(Note(text="two"), id="note:2")
            .link("note:1", "supported_by", "note:2")
        )


def test_trace_store_roundtrip(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    trace = RunTrace(
        id="abc",
        session_id="s1",
        outcome="completed",
        spans=[
            AgentSpan(
                agent="greeter",
                event_type="artifact_created",
                reads=[
                    ArtifactRef(
                        artifact_id="q1",
                        version=0,
                        op_type="read",
                        data_type="Question",
                        data='{"text":"hi"}',
                    )
                ],
                writes=[
                    ArtifactRef(
                        artifact_id="a1",
                        version=0,
                        op_type="create",
                        data_type="Answer",
                        data='{"text":"Привет!"}',
                    )
                ],
                latency_ms=12.5,
            )
        ],
    )
    run(store.export(trace))

    assert run(store.get("abc")) is not None
    assert run(store.get("nope")) is None
    assert run(store.query())["total"] == 1
    assert run(store.query(session_id="other"))["items"] == []
    assert run(store.query(session_id="s1"))["items"][0]["id"] == "abc"
    assert run(store.query(outcome="completed"))["total"] == 1
    assert run(store.query(outcome="failed"))["items"] == []


def test_runtime_records_spans_and_trace(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    ctx = Context(resources=RuntimeResources(llm=ReplyLLM()))
    tracer = Tracer(store=store)
    runtime = Runtime(ctx, agents=[Greeter()], tracer=tracer)
    ctx.create(Question(text="привет"))
    asyncio.run(runtime.arun())

    traces = run(store.query())["items"]
    assert len(traces) == 1
    trace = run(store.get(traces[0]["id"]))
    assert trace is not None
    assert trace.outcome == "completed"
    # greeter span: read the question, created the answer
    greeter = next(s for s in trace.spans if s.agent == "Greeter")
    assert any(
        r.artifact_id == ctx.list_artifacts(Question)[0].id for r in greeter.reads
    )
    # writes carry artifact data
    assert any(w.op_type == "create" for w in greeter.writes)
    assert greeter.latency_ms >= 0
    answers = ctx.list_artifacts(Answer)
    assert any(w.artifact_id == answers[0].id for w in greeter.writes)
    created = next(w for w in greeter.writes if w.op_type == "create")
    assert created.data_type == "Answer"
    assert created.data is not None and "Привет" in created.data


def test_runtime_records_llm_calls(tmp_path):
    from reactifact.structured import structured_llm

    class AnswerBody(BaseModel):
        text: str

    class LlamAgent(Agent):
        consumes = [Consume(Question)]

        async def run(self, event, context):
            body = await structured_llm(context, schema=AnswerBody, user="привет")
            return Patch().create(Answer(text=body.text if body else ""))

    store = TraceStore(str(tmp_path / "llm.db"))
    ctx = Context(resources=RuntimeResources(llm=ReplyLLM()))
    runtime = Runtime(ctx, agents=[LlamAgent()], tracer=Tracer(store=store))
    ctx.create(Question(text="hi"))
    asyncio.run(runtime.arun())

    trace = run(store.get(run(store.query())["items"][0]["id"]))
    assert trace is not None
    llam = next(s for s in trace.spans if s.agent == "LlamAgent")
    assert len(llam.llm_calls) == 1
    call = llam.llm_calls[0]
    assert call.agent == "LlamAgent"
    assert call.prompt_tokens == 12
    assert call.completion_tokens == 7
    assert call.latency_ms >= 0
    assert any("привет" in (m.get("content") or "") for m in call.messages)
    assert "привет" in call.response


def test_runtime_without_tracer_still_works():
    ctx = Context(resources=RuntimeResources(llm=ReplyLLM()))
    runtime = Runtime(ctx, agents=[Greeter()])
    ctx.create(Question(text="привет"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(Answer)


def test_composite_tracer_fans_out(tmp_path):
    store_a = TraceStore(str(tmp_path / "a.db"))
    store_b = TraceStore(str(tmp_path / "b.db"))
    ctx = Context(resources=RuntimeResources(llm=ReplyLLM()))
    runtime = Runtime(
        ctx,
        agents=[Greeter()],
        tracer=[Tracer(store=store_a), Tracer(store=store_b)],
    )
    ctx.create(Question(text="привет"))
    asyncio.run(runtime.arun())
    assert run(store_a.query())["total"] == 1
    assert run(store_b.query())["total"] == 1


class BoomSink:
    """A sink that is always unreachable (e.g. a down Langfuse)."""

    async def export(self, trace: RunTrace) -> None:
        raise RuntimeError("langfuse is down")


class CapturingSink:
    """Records the traces it received, to prove fan-out continued."""

    def __init__(self) -> None:
        self.traces: list[RunTrace] = []

    async def export(self, trace: RunTrace) -> None:
        self.traces.append(trace)


def test_failing_sink_does_not_break_the_run():
    """An unreachable sink is skipped; the run and healthy sinks still proceed."""
    ok = CapturingSink()
    ctx = Context(resources=RuntimeResources(llm=ReplyLLM()))
    runtime = Runtime(ctx, agents=[Greeter()], tracer=Tracer(sinks=[BoomSink(), ok]))
    ctx.create(Question(text="привет"))

    assert asyncio.run(runtime.arun()) >= 1
    assert ctx.list_artifacts(Answer)  # business outcome still produced
    assert len(ok.traces) == 1  # the healthy sibling still got the trace


class BoomTracer(Tracer):
    """A custom tracer whose every callback raises."""

    def on_turn_begin(self, run_id, *, session_id, started_at) -> None:
        raise RuntimeError("boom")

    def on_span(self, span) -> None:
        raise RuntimeError("boom")

    async def on_turn_end(self, trace: RunTrace) -> None:
        raise RuntimeError("boom")


def test_failing_custom_tracer_does_not_break_the_run():
    ctx = Context(resources=RuntimeResources(llm=ReplyLLM()))
    runtime = Runtime(ctx, agents=[Greeter()], tracer=BoomTracer())
    ctx.create(Question(text="привет"))

    assert asyncio.run(runtime.arun()) >= 1
    assert ctx.list_artifacts(Answer)


def test_composite_tracer_isolates_a_failing_member():
    ok = CapturingSink()
    ctx = Context(resources=RuntimeResources(llm=ReplyLLM()))
    runtime = Runtime(
        ctx, agents=[Greeter()], tracer=[BoomTracer(), Tracer(sinks=[ok])]
    )
    ctx.create(Question(text="привет"))

    assert asyncio.run(runtime.arun()) >= 1
    assert len(ok.traces) == 1  # the second tracer still received the trace


def test_recording_llm_ignores_a_failing_recorder():
    """A raising `on_call` must not fail the wrapped LLM call."""

    def boom(call: LLMCall) -> None:
        raise RuntimeError("tracer is down")

    llm = RecordingLLM(ReplyLLM(), on_call=boom, agent_of=lambda: "agent")
    response = asyncio.run(llm.complete(LLMRequest(messages=[Message.user("hi")])))

    assert response.text == '{"text":"привет"}'


def test_trace_store_migrates_old_schema(tmp_path):
    """Old DB without the llm_calls column — migrated without data loss."""
    import sqlite3

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL DEFAULT '',
            started_at REAL NOT NULL, duration_ms REAL NOT NULL, outcome TEXT NOT NULL
        );
        CREATE TABLE spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
            agent TEXT NOT NULL, event_type TEXT NOT NULL DEFAULT '', latency_ms REAL NOT NULL DEFAULT 0,
            error TEXT, reads TEXT NOT NULL DEFAULT '[]', writes TEXT NOT NULL DEFAULT '[]'
        );
        """
    )
    conn.execute("INSERT INTO runs VALUES ('old', 's', 0, 1, 'completed')")
    conn.commit()
    conn.close()

    store = TraceStore(path)  # migration adds llm_calls
    assert run(store.get("old")) is not None  # the old trace is readable
    rows = store._conn.execute("PRAGMA table_info(spans)").fetchall()
    assert any(r[1] == "llm_calls" for r in rows)


def test_trace_memoizes_artifact_dump(tmp_path):
    """Two agents read one artifact — model_dump is called once (memo)."""
    CountingQuestion.dumps = 0

    class Greeter2(Agent):
        consumes = [Consume(CountingQuestion)]

        async def run(self, event, context):
            return Patch().create(Answer(text="hi"))

    store = TraceStore(str(tmp_path / "memo.db"))
    ctx = Context(resources=RuntimeResources(llm=ReplyLLM()))
    runtime = Runtime(ctx, agents=[Greeter2(), Greeter2()], tracer=Tracer(store=store))
    ctx.create(CountingQuestion(text="q"))
    asyncio.run(runtime.arun())

    assert CountingQuestion.dumps == 1


def test_trace_store_retention_prunes_oldest(tmp_path):
    store = TraceStore(str(tmp_path / "ret.db"), max_runs=2)
    for i in range(5):
        run(store.export(RunTrace(id=f"r{i}", session_id="s", outcome="completed")))

    items = run(store.query())["items"]
    ids = {item["id"] for item in items}
    assert ids == {"r3", "r4"}  # the last 2 remain
    assert run(store.get("r0")) is None
    # spans of old traces are also removed
    rows = store._conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
    assert rows == 0  # r3/r4 have no spans (exported without spans)


class FakeClient:
    """Captures POST requests instead of real HTTP (async, like httpx)."""

    def __init__(self):
        self.requests: list[tuple[str, dict]] = []

    async def post(self, url: str, json=None):
        self.requests.append((url, json or {}))


def test_langfuse_exports_trace_spans_and_llm():
    from reactifact.tracing import LangfuseTracer

    client = FakeClient()
    langfuse = LangfuseTracer(
        public_key="pk",
        secret_key="sk",
        host="http://langfuse.local",
        client=client,
    )
    run(
        langfuse.on_turn_end(
            RunTrace(
                id="tr",
                session_id="s1",
                outcome="completed",
                spans=[
                    AgentSpan(
                        agent="greeter",
                        event_type="artifact_created",
                        writes=[
                            ArtifactRef(
                                artifact_id="a1",
                                version=0,
                                op_type="create",
                                data_type="Answer",
                                data='{"t":"Привет!"}',
                            )
                        ],
                        llm_calls=[
                            LLMCall(
                                agent="greeter",
                                provider="fake",
                                model="m",
                                messages=[{"role": "user", "content": "hi"}],
                                response="ok",
                                prompt_tokens=3,
                                completion_tokens=2,
                            )
                        ],
                    )
                ],
            )
        )
    )

    # OTLP/HTTP: a single POST to the otel traces endpoint, not the deprecated
    # /api/public/traces + /api/public/observations REST ingestion (410/404 on
    # Langfuse v4, sunset on Cloud 2026-11-16).
    assert len(client.requests) == 1
    url, body = client.requests[0]
    assert url.endswith("/api/public/otel/v1/traces")
    assert langfuse._headers["x-langfuse-ingestion-version"] == "4"
    assert langfuse._headers["Authorization"].startswith("Basic ")

    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 3  # root run span + agent span + llm generation span
    by_name = {s["name"]: s for s in spans}

    def attr(span: dict, key: str):
        for a in span["attributes"]:
            if a["key"] == key:
                return a["value"]
        raise KeyError(key)

    root = by_name["reactifact run"]
    assert attr(root, "langfuse.session.id") == {"stringValue": "s1"}
    assert attr(root, "langfuse.observation.type") == {"stringValue": "span"}

    agent_span = by_name["greeter"]
    assert agent_span["parentSpanId"] == root["spanId"]
    assert attr(agent_span, "langfuse.observation.type") == {"stringValue": "span"}
    write_summary = json.loads(
        attr(agent_span, "langfuse.observation.metadata.write_summary")["stringValue"]
    )
    assert write_summary == {"Answer": 1}
    output = json.loads(attr(agent_span, "langfuse.observation.output")["stringValue"])
    assert output[0]["artifact_id"] == "a1"

    llm_span = by_name["llm:m"]
    assert llm_span["parentSpanId"] == agent_span["spanId"]
    assert attr(llm_span, "langfuse.observation.type") == {"stringValue": "generation"}
    assert attr(llm_span, "gen_ai.usage.input_tokens") == {"intValue": "3"}
    assert attr(llm_span, "gen_ai.usage.output_tokens") == {"intValue": "2"}
    assert attr(llm_span, "gen_ai.request.model") == {"stringValue": "m"}


def test_otlp_tracer_exports_vendor_neutral_spans():
    from reactifact.tracing import OTLPTracer

    client = FakeClient()
    tracer = OTLPTracer(
        endpoint="http://localhost:4318/v1/traces",
        service_name="my-app",
        client=client,
    )
    run(
        tracer.on_turn_end(
            RunTrace(
                id="tr",
                session_id="s1",
                outcome="completed",
                spans=[
                    AgentSpan(
                        agent="greeter",
                        event_type="artifact_created",
                        writes=[
                            ArtifactRef(
                                artifact_id="a1",
                                version=0,
                                op_type="create",
                                data_type="Answer",
                                data='{"t":"hi"}',
                            )
                        ],
                        llm_calls=[
                            LLMCall(
                                agent="greeter",
                                provider="fake",
                                model="m",
                                messages=[{"role": "user", "content": "hi"}],
                                response="ok",
                                prompt_tokens=3,
                                completion_tokens=2,
                            )
                        ],
                    )
                ],
            )
        )
    )

    assert len(client.requests) == 1
    url, body = client.requests[0]
    assert url == "http://localhost:4318/v1/traces"

    resource_spans = body["resourceSpans"][0]
    spans = resource_spans["scopeSpans"][0]["spans"]
    assert len(spans) == 3  # root run span + agent span + llm chat span

    def attr(node: dict, key: str):
        for a in node["attributes"]:
            if a["key"] == key:
                return a["value"]
        raise KeyError(key)

    assert attr(resource_spans["resource"], "service.name") == {"stringValue": "my-app"}
    # no langfuse.* keys anywhere — vendor-neutral only
    for span in spans:
        assert not any(a["key"].startswith("langfuse.") for a in span["attributes"])

    by_name = {s["name"]: s for s in spans}
    root = by_name["reactifact run"]
    assert attr(root, "reactifact.session.id") == {"stringValue": "s1"}

    agent_span = by_name["invoke_agent greeter"]
    assert agent_span["parentSpanId"] == root["spanId"]
    assert attr(agent_span, "gen_ai.agent.name") == {"stringValue": "greeter"}

    llm_span = by_name["chat m"]
    assert llm_span["parentSpanId"] == agent_span["spanId"]
    assert attr(llm_span, "gen_ai.usage.input_tokens") == {"intValue": "3"}
    assert attr(llm_span, "gen_ai.usage.output_tokens") == {"intValue": "2"}
    assert attr(llm_span, "gen_ai.request.model") == {"stringValue": "m"}


def test_postgres_store_requires_pg_extra():
    """PostgresStore without psycopg installed fails honestly (pg extra)."""
    import importlib.util

    from reactifact.tracing import PostgresStore

    if importlib.util.find_spec("psycopg") is None:
        try:
            PostgresStore("postgresql://x")
        except (ImportError, ModuleNotFoundError):
            pass
        else:
            raise AssertionError("expected ImportError without psycopg")


def _basic(user: str, password: str) -> str:
    import base64

    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def test_trace_router_basic_auth(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from reactifact.tracing.web import create_trace_router

    store = TraceStore(str(tmp_path / "auth.db"))
    run(store.export(RunTrace(id="r1", session_id="s", outcome="completed")))
    app = FastAPI()
    app.include_router(create_trace_router(store, username="obs", password="secret"))
    client = TestClient(app)

    # without a header — 401
    assert client.get("/api/traces").status_code == 401
    assert client.get("/traces").status_code == 401
    # wrong password — 401
    assert (
        client.get(
            "/api/traces", headers={"Authorization": _basic("obs", "bad")}
        ).status_code
        == 401
    )
    # valid — 200 and data
    ok = client.get("/api/traces", headers={"Authorization": _basic("obs", "secret")})
    assert ok.status_code == 200
    assert ok.json()["total"] == 1


def test_trace_router_open_without_auth(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from reactifact.tracing.web import create_trace_router

    store = TraceStore(str(tmp_path / "open.db"))
    app = FastAPI()
    app.include_router(create_trace_router(store))
    client = TestClient(app)
    assert client.get("/api/traces").status_code == 200


def test_trace_run_page_embeds_mermaid_diagram(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from reactifact.tracing.web import create_trace_router

    store = TraceStore(str(tmp_path / "diag.db"))
    run(
        store.export(
            RunTrace(
                id="r1",
                outcome="completed",
                duration_ms=12.0,
                spans=[
                    AgentSpan(agent="planner", event_type="ResearchTurn", writes=[])
                ],
            )
        )
    )
    app = FastAPI()
    app.include_router(create_trace_router(store))
    client = TestClient(app)
    html = client.get("/traces/r1").text
    assert "__MERMAID__" not in html  # the placeholder was replaced
    assert "__RUN_ID__" not in html
    assert "sequenceDiagram" in html
    assert "MERMAID_SRC" in html


def test_trace_run_page_embeds_provenance_graph(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from reactifact.tracing.models import RelationRef
    from reactifact.tracing.web import create_trace_router

    store = TraceStore(str(tmp_path / "evg.db"))
    run(
        store.export(
            RunTrace(
                id="r1",
                outcome="completed",
                duration_ms=12.0,
                spans=[
                    AgentSpan(
                        agent="answerer",
                        event_type="Answer",
                        writes=[
                            {
                                "artifact_id": "a1",
                                "op_type": "create",
                                "data_type": "Answer",
                                "data": "{}",
                            },
                            {
                                "artifact_id": "c1",
                                "op_type": "create",
                                "data_type": "Claim",
                                "data": "{}",
                            },
                        ],
                        relations=[
                            RelationRef(
                                source_id="a1",
                                relation="supported_by",
                                target_id="c1",
                                source_type="Answer",
                                target_type="Claim",
                            )
                        ],
                    )
                ],
            )
        )
    )
    app = FastAPI()
    app.include_router(create_trace_router(store))
    client = TestClient(app)
    html = client.get("/traces/r1").text
    assert "__MERMAID_GRAPH__" not in html
    assert "Evidence graph" in html
    assert "MERMAID_GRAPH_SRC" in html
    assert "supported_by" in html  # the provenance edge was rendered server-side


def test_runtime_records_provenance_relations(tmp_path):
    store = TraceStore(str(tmp_path / "rels.db"))
    ctx = Context(resources=RuntimeResources())
    runtime = Runtime(ctx, agents=[Linker()], tracer=Tracer(store=store))
    ctx.create(Question(text="link me"))
    asyncio.run(runtime.arun())

    items = run(store.query())["items"]
    assert len(items) == 1
    loaded = run(store.get(items[0]["id"]))
    assert loaded is not None
    assert loaded.spans and loaded.spans[0].writes
    relations = loaded.spans[0].relations
    assert len(relations) == 1
    edge = relations[0]
    assert edge.source_id == "note:1"
    assert edge.relation == "supported_by"
    assert edge.target_id == "note:2"
    assert edge.source_type == "Note"
    assert edge.target_type == "Note"


def test_postgres_store_roundtrip(tmp_path):
    """Postgres async write+read against a real server (TEST_PG_DSN)."""
    import os

    import pytest
    from reactifact.tracing import PostgresStore

    dsn = os.environ.get("TEST_PG_DSN")
    if not dsn:
        pytest.skip("TEST_PG_DSN not set — skipping Postgres trace integration")
    import psycopg  # noqa: F401  (ensure the extra is installed)

    store = PostgresStore(dsn)
    trace = RunTrace(
        id="pg-1",
        session_id="s",
        outcome="completed",
        spans=[
            AgentSpan(
                agent="greeter",
                event_type="artifact_created",
                writes=[
                    ArtifactRef(
                        artifact_id="a1",
                        op_type="create",
                        data_type="Answer",
                        data='{"text":"hi"}',
                    )
                ],
                relations=[
                    RelationRef(
                        source_id="a1",
                        relation="supported_by",
                        target_id="c1",
                        source_type="Answer",
                        target_type="Claim",
                    )
                ],
            )
        ],
    )
    run(store.export(trace))
    assert run(store.get("pg-1")) is not None
    items = run(store.query(session_id="s"))["items"]
    assert any(i["id"] == "pg-1" for i in items)
    loaded = run(store.get("pg-1"))
    assert loaded is not None
    assert loaded.spans[0].relations[0].relation == "supported_by"


def test_recording_llm_copies_prompt_hash():
    from reactifact.providers import FakeLLM, LLMRequest, Message
    from reactifact.tracing import LLMCall, RecordingLLM

    recorded: list[LLMCall] = []
    llm = RecordingLLM(
        FakeLLM('{"text":"x"}'),
        on_call=recorded.append,
        agent_of=lambda: "a",
    )
    request = LLMRequest(messages=[Message.user("hi")], prompt_hash="abc123")
    asyncio.run(llm.complete(request))

    assert recorded[0].prompt_hash == "abc123"


def test_recording_llm_prompt_hash_defaults_empty():
    from reactifact.providers import FakeLLM, LLMRequest, Message
    from reactifact.tracing import LLMCall, RecordingLLM

    recorded: list[LLMCall] = []
    llm = RecordingLLM(FakeLLM("x"), on_call=recorded.append, agent_of=lambda: "a")
    asyncio.run(llm.complete(LLMRequest(messages=[Message.user("hi")])))
    assert recorded[0].prompt_hash == ""
