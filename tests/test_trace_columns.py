"""Configurable, artifact-derived columns for the traces table (`TraceColumn`)."""

import asyncio
import json

from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Runtime, RuntimeResources
from reactifact.tracing import RunTrace, TraceColumn, Tracer, TraceStore
from reactifact.tracing.columns import SpanArtifacts, extract_columns
from reactifact.tracing.models import ArtifactRef


def run(coro):
    return asyncio.run(coro)


def _ref(data_type: str, data, op_type: str = "create") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=data_type.lower(),
        op_type=op_type,
        data_type=data_type,
        data=json.dumps(data),
    )


def _spans() -> list[SpanArtifacts]:
    return [
        SpanArtifacts(
            agent="route",
            reads=[_ref("UserMsg", {"text": "упал под"})],
        ),
        SpanArtifacts(
            agent="render",
            writes=[
                _ref(
                    "ChatReply",
                    {
                        "text": "всё ок",
                        "ok": True,
                        "sources": [{"title": "S1"}, {"title": "S2"}],
                    },
                )
            ],
        ),
    ]


def test_extract_by_agent_type_and_direction():
    cols = [
        TraceColumn(
            label="Q", agent="route", type="UserMsg", field="text", direction="read"
        ),
        TraceColumn(label="A", agent="render", type="ChatReply", field="text"),
    ]
    assert extract_columns(_spans(), cols) == {"Q": "упал под", "A": "всё ок"}


def test_extract_list_auto_first_and_explicit_index():
    cols = [
        TraceColumn(label="first", type="ChatReply", field="sources.title"),
        TraceColumn(label="second", type="ChatReply", field="sources.1.title"),
        TraceColumn(label="last", type="ChatReply", field="sources.-1.title"),
    ]
    assert extract_columns(_spans(), cols) == {
        "first": "S1",
        "second": "S2",
        "last": "S2",
    }


def test_extract_whole_object_and_bool_and_missing():
    cols = [
        TraceColumn(label="all", type="ChatReply", field=""),
        TraceColumn(label="flag", type="ChatReply", field="ok"),
        TraceColumn(label="missing", type="ChatReply", field="nope.deep"),
        TraceColumn(label="absent-type", type="Nope", field="text", default="?"),
    ]
    out = extract_columns(_spans(), cols)
    assert out["flag"] == "true"
    assert out["missing"] == "—"
    assert out["absent-type"] == "?"
    assert json.loads(out["all"])["text"] == "всё ок"


def test_extract_wrong_agent_or_truncated_data_falls_back():
    cols = [TraceColumn(label="x", agent="nobody", type="ChatReply", field="text")]
    assert extract_columns(_spans(), cols) == {"x": "—"}
    truncated = [
        SpanArtifacts(
            agent="render",
            writes=[
                ArtifactRef(
                    artifact_id="x",
                    data_type="ChatReply",
                    data='{"text": "ok", "cut',
                )
            ],
        )
    ]
    assert extract_columns(truncated, cols) == {"x": "—"}


def test_extract_index_picks_among_matches():
    spans = [
        SpanArtifacts(agent="a", writes=[_ref("Note", {"n": 1})]),
        SpanArtifacts(agent="a", writes=[_ref("Note", {"n": 2})]),
        SpanArtifacts(agent="a", writes=[_ref("Note", {"n": 3})]),
    ]
    assert extract_columns(spans, [TraceColumn(label="x", field="n")]) == {"x": "3"}
    assert extract_columns(spans, [TraceColumn(label="x", field="n", index=1)]) == {
        "x": "2"
    }
    assert extract_columns(spans, [TraceColumn(label="x", field="n", index=-2)]) == {
        "x": "2"
    }


# ---- integration: runtime -> store -> query --------------------------------- #


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str
    sources: list[dict]


class Producer(Agent):
    name = "producer"
    consumes = [Consume(Question)]

    async def run(self, event, context):
        return Patch().create(
            Answer(text="done", sources=[{"title": "A"}, {"title": "B"}])
        )


def _seed(tmp_path, **resources_kw):
    store = TraceStore(str(tmp_path / "traces.db"), max_runs=None)
    ctx = Context(resources=RuntimeResources(**resources_kw))
    runtime = Runtime(ctx, agents=[Producer()], tracer=Tracer(store=store))
    ctx.create(Question(text="hello"))
    run(runtime.arun())
    return store


def test_session_scope_shows_question_and_answer_on_every_run(tmp_path):
    """A HITL clarify splits a session into runs; session scope shows Q/A on all."""
    from reactifact.tracing import AgentSpan

    store = TraceStore(str(tmp_path / "sess.db"), max_runs=None)
    run(
        store.export(
            RunTrace(
                id="r1",
                session_id="s1",
                outcome="completed",
                spans=[
                    AgentSpan(
                        agent="route",
                        event_type="e",
                        reads=[_ref("UserMsg", {"text": "why?"})],
                    ),
                    AgentSpan(
                        agent="k8s",
                        event_type="e",
                        writes=[_ref("PendingQuestion", {"question": "ns?"})],
                    ),
                ],
            )
        )
    )
    run(
        store.export(
            RunTrace(
                id="r2",
                session_id="s1",
                outcome="completed",
                spans=[
                    AgentSpan(agent="k8s", event_type="e"),
                    AgentSpan(
                        agent="render",
                        event_type="e",
                        writes=[_ref("ChatReply", {"text": "because"})],
                    ),
                ],
            )
        )
    )

    cols = [
        TraceColumn(
            label="Question",
            agent="route",
            type="UserMsg",
            field="text",
            direction="read",
            scope="session",
        ),
        TraceColumn(label="Answer", type="ChatReply", field="text", scope="session"),
    ]
    items = {i["id"]: i for i in run(store.query(columns=cols))["items"]}
    # as of each run: the ask run has the question but no answer yet; the resume
    # run has both (the question is the most recent message so far).
    assert items["r1"]["fields"] == {"Question": "why?", "Answer": "—"}
    assert items["r2"]["fields"] == {"Question": "why?", "Answer": "because"}
    # the run detail resolves the same fields (session-aware)
    assert run(store.field_values("r2", cols)) == {
        "Question": "why?",
        "Answer": "because",
    }

    # run scope would strand each value on its own run
    run_cols = [
        TraceColumn(
            label="Question",
            agent="route",
            type="UserMsg",
            field="text",
            direction="read",
        ),
        TraceColumn(label="Answer", type="ChatReply", field="text"),
    ]
    run_items = {i["id"]: i for i in run(store.query(columns=run_cols))["items"]}
    assert run_items["r1"]["fields"] == {"Question": "why?", "Answer": "—"}
    assert run_items["r2"]["fields"] == {"Question": "—", "Answer": "because"}


def test_session_question_is_the_latest_message_so_far(tmp_path):
    """Each run reads its trigger first (newest), then older inputs — the
    session-scoped question must resolve to the run's own message, not the
    session's first one."""
    from datetime import UTC, datetime, timedelta

    from reactifact.tracing import AgentSpan

    store = TraceStore(str(tmp_path / "chat.db"), max_runs=None)
    base = datetime(2026, 1, 1, tzinfo=UTC)

    def span(*texts):
        return AgentSpan(
            agent="flow",
            event_type="e",
            reads=[_ref("UserMsg", {"text": t}) for t in texts],
        )

    run(
        store.export(
            RunTrace(
                id="r1",
                session_id="s",
                started_at=base,
                spans=[span("m1")],
            )
        )
    )
    run(
        store.export(
            RunTrace(
                id="r2",
                session_id="s",
                started_at=base + timedelta(seconds=1),
                spans=[span("m2", "m1")],  # trigger first, then older inputs
            )
        )
    )
    run(
        store.export(
            RunTrace(
                id="r3",
                session_id="s",
                started_at=base + timedelta(seconds=2),
                spans=[span("m3", "m2", "m1")],
            )
        )
    )

    col = [
        TraceColumn(
            label="Q",
            agent="flow",
            type="UserMsg",
            field="text",
            direction="read",
            scope="session",
        )
    ]
    items = {i["id"]: i for i in run(store.query(columns=col))["items"]}
    assert items["r1"]["fields"]["Q"] == "m1"
    assert items["r2"]["fields"]["Q"] == "m2"
    assert items["r3"]["fields"]["Q"] == "m3"


def test_store_query_computes_configured_fields(tmp_path):
    store = _seed(tmp_path)
    cols = [
        TraceColumn(
            label="Q", agent="producer", type=Question, field="text", direction="read"
        ),
        TraceColumn(label="A", agent="producer", type=Answer, field="text"),
        TraceColumn(label="Src", agent="producer", type=Answer, field="sources.title"),
    ]
    items = run(store.query(columns=cols))["items"]
    assert items[0]["fields"] == {"Q": "hello", "A": "done", "Src": "A"}
    # no columns configured -> no fields payload
    assert run(store.query())["items"][0]["fields"] == {}


class BigAnswer(BaseModel):
    text: str
    deep: dict[str, str]


class BigProducer(Agent):
    name = "producer"
    consumes = [Consume(Question)]

    async def run(self, event, context):
        return Patch().create(BigAnswer(text="x" * 400, deep={"k": "found"}))


def test_trace_truncate_is_configurable(tmp_path):
    cols = [TraceColumn(label="deep", field="deep.k")]

    def seed_with(truncate, name):
        d = tmp_path / name
        d.mkdir()
        store = TraceStore(str(d / "traces.db"), max_runs=None)
        ctx = Context(resources=RuntimeResources(trace_truncate=truncate))
        runtime = Runtime(ctx, agents=[BigProducer()], tracer=Tracer(store=store))
        ctx.create(Question(text="hello"))
        run(runtime.arun())
        return store

    # a tiny limit cuts the JSON mid-structure -> unparseable -> default
    small = seed_with(20, "small")
    assert run(small.query(columns=cols))["items"][0]["fields"] == {"deep": "—"}
    # truncation disabled -> the deep field resolves
    full = seed_with(None, "full")
    assert run(full.query(columns=cols))["items"][0]["fields"] == {"deep": "found"}


# ---- API -------------------------------------------------------------------- #


def _client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from reactifact.tracing.web import create_trace_router

    store = _seed(tmp_path)
    app = FastAPI()
    app.include_router(
        create_trace_router(
            store,
            columns=[
                TraceColumn(
                    label="Question",
                    agent="producer",
                    type="Question",
                    field="text",
                    direction="read",
                ),
                TraceColumn(
                    label="Answer", agent="producer", type="Answer", field="text"
                ),
            ],
        )
    )
    return TestClient(app)


def test_api_columns_and_fields(tmp_path):
    client = _client(tmp_path)
    assert [c["label"] for c in client.get("/api/columns").json()["items"]] == [
        "Question",
        "Answer",
    ]
    item = client.get("/api/traces").json()["items"][0]
    assert item["fields"] == {"Question": "hello", "Answer": "done"}
    # the run detail carries the same fields
    detail = client.get(f"/api/traces/{item['id']}").json()
    assert detail["fields"] == {"Question": "hello", "Answer": "done"}
    # the table page wires the chooser
    html = client.get("/traces").text
    assert "col-menu" in html and "/api/columns" in html
