"""Trace read/write limits: pagination, write caps, deletion."""

from __future__ import annotations

import asyncio
import warnings

import pytest
from pydantic import BaseModel
from reactifact import Agent, Context, RuntimeResources
from reactifact.events import Event, EventType
from reactifact.tracing import AgentSpan, RunTrace, TraceLimits, Tracer, TraceStore
from reactifact.tracing.tracer import RunTracer


class Question(BaseModel):
    text: str


def run(coro):
    return asyncio.run(coro)


def test_get_is_paginated_and_reports_totals(tmp_path):
    store = TraceStore(str(tmp_path / "traces.db"))
    run(
        store.export(
            RunTrace(
                id="r1",
                session_id="s1",
                outcome="completed",
                spans=[AgentSpan(agent=f"a{i}") for i in range(5)],
            )
        )
    )

    first = run(store.get("r1", span_limit=2))
    assert first is not None
    assert [s.agent for s in first.spans] == ["a0", "a1"]
    assert first.span_total == 5
    assert first.span_offset == 0
    assert first.has_more_spans is True

    last = run(store.get("r1", span_limit=2, span_offset=4))
    assert last is not None
    assert [s.agent for s in last.spans] == ["a4"]
    assert last.has_more_spans is False


def test_write_time_span_cap_marks_trace_truncated(tmp_path):
    store = TraceStore(str(tmp_path / "cap.db"))
    ctx = Context(resources=RuntimeResources())
    tracer = RunTracer(ctx, Tracer(store=store, limits=TraceLimits(max_spans=2)))
    tracer.begin_turn(session_id="s")
    agent = Agent()
    event = Event(EventType.ARTIFACT_CREATED, Question, "q1")
    for _ in range(4):
        tracer.record_span(agent, event, [], 1.0)
    run(tracer.end_turn(session_id="s", duration_ms=4.0, outcome="completed"))

    loaded = run(store.get(tracer.run_id))
    assert loaded is not None
    assert len(loaded.spans) == 2
    assert loaded.span_total == 2
    assert loaded.trace_truncated is True


def test_delete_run_and_session(tmp_path):
    store = TraceStore(str(tmp_path / "delete.db"))
    for rid in ("r1", "r2"):
        run(
            store.export(
                RunTrace(
                    id=rid,
                    session_id="s1",
                    outcome="completed",
                    spans=[AgentSpan(agent="a")],
                )
            )
        )

    assert run(store.delete_run("r1")) is True
    assert run(store.get("r1")) is None
    assert run(store.get("r2")) is not None
    assert run(store.delete_session("s1")) == 1
    assert run(store.get("r2")) is None


def test_trace_api_deletes_and_bounds_the_diagram(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from reactifact.tracing.web import create_trace_router

    store = TraceStore(str(tmp_path / "api.db"))
    run(
        store.export(
            RunTrace(
                id="r1",
                session_id="s1",
                outcome="completed",
                spans=[AgentSpan(agent=f"a{i}") for i in range(3)],
            )
        )
    )
    app = FastAPI()
    app.include_router(create_trace_router(store, max_mermaid_spans=2))
    client = TestClient(app)

    tags = client.get("/api/traces/r1/tags")
    assert tags.status_code == 200
    assert tags.json()["items"] == []

    diagram = client.get("/api/traces/r1/diagram")
    assert diagram.status_code == 200
    assert diagram.json()["truncated"] is True
    assert diagram.json()["span_total"] == 3
    assert diagram.json()["span_limit"] == 2

    assert client.delete("/api/traces/r1").json() == {"deleted": 1}
    assert client.get("/api/traces/r1").status_code == 404


def test_trace_truncate_is_deprecated():
    with pytest.warns(DeprecationWarning, match="TraceLimits"):
        resources = RuntimeResources(trace_truncate=100)
    assert resources.trace_truncate == 100

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        default = RuntimeResources()
    assert default.trace_truncate == 1500


def test_sqlite_export_rolls_back_when_prune_fails(tmp_path, monkeypatch):
    store = TraceStore(str(tmp_path / "atomic.db"), max_runs=100)

    def boom(keep: int, *, commit: bool = True) -> int:
        raise RuntimeError("prune failed")

    monkeypatch.setattr(store, "_prune_sync", boom)
    with pytest.raises(RuntimeError, match="prune failed"):
        run(
            store.export(
                RunTrace(
                    id="r1",
                    session_id="s1",
                    outcome="completed",
                    spans=[AgentSpan(agent="a")],
                )
            )
        )

    # insert + retention share one transaction, so the failed prune leaves no run
    assert run(store.get("r1")) is None
