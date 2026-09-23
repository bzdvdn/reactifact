"""Reviewer annotations on traces: tags, notes, facets, filtering, vocabulary.

Tags are the UI review workflow (§54) — a human marks a run after inspecting it
so it can be found again by tag and acted on. They are stored by the sink, not
produced by the runtime, so these tests exercise the stores directly and the
dashboard API on top of them.
"""

import asyncio
import os
import sqlite3
import uuid

import pytest
from reactifact.tracing import (
    AgentSpan,
    LLMCall,
    RunTrace,
    TagAssignment,
    TraceStore,
)


def run(coro):
    return asyncio.run(coro)


def _seed(store: TraceStore, run_id: str, **kwargs) -> None:
    run(store.export(RunTrace(id=run_id, **kwargs)))


def test_tag_lifecycle_and_facets(tmp_path):
    store = TraceStore(str(tmp_path / "tags.db"))
    _seed(store, "r1", session_id="s1", outcome="completed")
    _seed(store, "r2", session_id="s2", outcome="failed")

    assert run(store.tag_runs(["r1", "r2"], "bad-prompt", note="hallucinated")) == 2
    assert run(store.tag_runs(["r1"], "bad-prompt", note="updated note")) == 1
    assert run(store.tag_runs(["missing"], "ghost")) == 0

    facets = run(store.list_tags())
    by_name = {f["name"]: f for f in facets}
    assert by_name["bad-prompt"]["count"] == 2
    assert "ghost" not in by_name  # a typo'd run id seeds no vocabulary

    r1 = run(store.get("r1"))
    assert r1 is not None
    assert [(a.tag, a.note) for a in r1.annotations] == [("bad-prompt", "updated note")]

    assert run(store.untag_runs(["r1"], "bad-prompt")) == 1
    assert {f["name"]: f["count"] for f in run(store.list_tags())}["bad-prompt"] == 1


def test_query_filters_by_tag_any_and_all(tmp_path):
    store = TraceStore(str(tmp_path / "f.db"))
    for rid in ("r1", "r2", "r3"):
        _seed(store, rid, outcome="completed")
    run(store.tag_runs(["r1"], "a"))
    run(store.tag_runs(["r1", "r2"], "b"))

    assert {i["id"] for i in run(store.query(tags=["a"]))["items"]} == {"r1"}
    assert {i["id"] for i in run(store.query(tags=["a", "b"]))["items"]} == {"r1", "r2"}
    assert {
        i["id"] for i in run(store.query(tags=["a", "b"], tag_mode="all"))["items"]
    } == {"r1"}
    assert run(store.query(tags=["a", "b"], tag_mode="all"))["total"] == 1

    # list items carry their tags for rendering
    items = {i["id"]: i for i in run(store.query())["items"]}
    assert {t["tag"] for t in items["r2"]["tags"]} == {"b"}
    assert items["r3"]["tags"] == []


def test_query_search_and_sort(tmp_path):
    store = TraceStore(str(tmp_path / "q.db"))
    _seed(
        store,
        "r1",
        session_id="alpha",
        outcome="completed",
        duration_ms=30,
        spans=[AgentSpan(agent="planner", event_type="e", error="boom")],
    )
    _seed(store, "r2", session_id="beta", outcome="failed", duration_ms=10)

    assert [i["id"] for i in run(store.query(q="planner"))["items"]] == ["r1"]
    assert [i["id"] for i in run(store.query(q="boom"))["items"]] == ["r1"]
    assert [i["id"] for i in run(store.query(q="beta"))["items"]] == ["r2"]

    asc = run(store.query(sort="duration_ms", order="asc"))["items"]
    assert [i["id"] for i in asc] == ["r2", "r1"]
    desc = run(store.query(sort="duration_ms", order="desc"))["items"]
    assert [i["id"] for i in desc] == ["r1", "r2"]


def test_query_search_escapes_like_wildcards(tmp_path):
    """A `%` or `_` in the query is literal, not a SQL wildcard."""
    store = TraceStore(str(tmp_path / "like.db"))
    _seed(store, "r1", session_id="100%_done", outcome="completed")
    _seed(store, "r2", session_id="other", outcome="completed")

    assert [i["id"] for i in run(store.query(q="100%"))["items"]] == ["r1"]
    assert [i["id"] for i in run(store.query(q="100%_done"))["items"]] == ["r1"]
    assert run(store.query(q="o_her"))["items"] == []  # `_` is not "any char"
    assert run(store.query(q="oth_r"))["items"] == []


def test_query_stats_and_token_rollup(tmp_path):
    store = TraceStore(str(tmp_path / "st.db"))
    _seed(
        store,
        "r1",
        duration_ms=10,
        spans=[
            AgentSpan(
                agent="a",
                event_type="e",
                llm_calls=[
                    LLMCall(agent="a", prompt_tokens=3, completion_tokens=2),
                    LLMCall(agent="a", prompt_tokens=1, completion_tokens=4),
                ],
            )
        ],
    )
    _seed(store, "r2", duration_ms=20)

    result = run(store.query())
    assert result["stats"] == {
        "runs": 2,
        "duration_ms": 30.0,
        "prompt_tokens": 4,
        "completion_tokens": 6,
    }
    by_id = {i["id"]: i for i in result["items"]}
    assert (by_id["r1"]["prompt_tokens"], by_id["r1"]["completion_tokens"]) == (4, 6)


def test_rename_and_delete_tag(tmp_path):
    store = TraceStore(str(tmp_path / "vocab.db"))
    _seed(store, "r1", outcome="completed")
    run(store.tag_runs(["r1"], "old", color="#ff0000"))
    run(store.rename_tag("old", "new"))

    r1 = run(store.get("r1"))
    assert r1 is not None and [a.tag for a in r1.annotations] == ["new"]
    assert run(store.list_tags())[0]["color"] == "#ff0000"

    run(store.delete_tag("new"))
    assert run(store.list_tags()) == []
    r1 = run(store.get("r1"))
    assert r1 is not None and r1.annotations == []


def test_rename_tag_merges_existing_assignment(tmp_path):
    store = TraceStore(str(tmp_path / "merge.db"))
    _seed(store, "r1", outcome="completed")
    run(store.tag_runs(["r1"], "a"))
    run(store.tag_runs(["r1"], "b"))
    run(store.rename_tag("a", "b"))  # r1 now carries both -> merge

    r1 = run(store.get("r1"))
    assert r1 is not None
    assert [a.tag for a in r1.annotations] == ["b"]


def test_annotations_survive_prune_for_kept_runs(tmp_path):
    store = TraceStore(str(tmp_path / "prune.db"), max_runs=2)
    for rid in ("r1", "r2", "r3"):
        _seed(store, rid, outcome="completed")
    run(store.tag_runs(["r1", "r3"], "keep"))
    # r1 is pruned away with its assignment; r3's survives.
    assert run(store.get("r1")) is None
    r3 = run(store.get("r3"))
    assert r3 is not None and [a.tag for a in r3.annotations] == ["keep"]
    assert run(store.list_tags())[0]["count"] == 1


def test_migrates_old_schema_without_token_columns(tmp_path):
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

    store = TraceStore(path)
    cols = {r[1] for r in store._conn.execute("PRAGMA table_info(runs)")}
    assert {"prompt_tokens", "completion_tokens"} <= cols
    # old trace is readable and annotatable
    assert run(store.get("old")) is not None
    assert run(store.tag_runs(["old"], "legacy")) == 1
    old = run(store.get("old"))
    assert old is not None and [a.tag for a in old.annotations] == ["legacy"]


def test_run_trace_to_dict_includes_annotations():
    trace = RunTrace(id="x", annotations=[TagAssignment(tag="t", note="n")])
    assert trace.to_dict()["annotations"][0]["tag"] == "t"
    assert RunTrace(id="y").to_dict()["annotations"] == []


# ---- dashboard API --------------------------------------------------------- #


def _client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from reactifact.tracing.web import create_trace_router

    store = TraceStore(str(tmp_path / "api.db"))
    _seed(store, "r1", session_id="s1", outcome="completed")
    _seed(store, "r2", session_id="s2", outcome="failed")
    app = FastAPI()
    app.include_router(create_trace_router(store))
    return TestClient(app)


def test_api_assign_filter_and_facets(tmp_path):
    client = _client(tmp_path)
    assert client.post(
        "/api/traces/r1/tags", json={"tag": "bad-prompt", "note": "hallucinated"}
    ).json() == {"tagged": 1}
    assert client.post("/api/traces/nope/tags", json={"tag": "x"}).status_code == 404

    listed = client.get("/api/traces", params={"tag": "bad-prompt"}).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == "r1"
    assert listed["items"][0]["tags"][0]["note"] == "hallucinated"

    facets = client.get("/api/tags").json()
    assert facets["total"] == 1 and facets["items"][0]["count"] == 1

    detail = client.get("/api/traces/r1").json()
    assert detail["annotations"][0]["tag"] == "bad-prompt"


def test_api_bulk_tag_and_remove(tmp_path):
    client = _client(tmp_path)
    assert client.post(
        "/api/traces/tags", json={"run_ids": ["r1", "r2"], "tag": "review"}
    ).json() == {"tagged": 2}
    assert client.get("/api/traces", params={"tag": "review"}).json()["total"] == 2

    assert client.post(
        "/api/traces/tags/remove", json={"run_ids": ["r2"], "tag": "review"}
    ).json() == {"untagged": 1}
    assert client.get("/api/traces", params={"tag": "review"}).json()["total"] == 1


def test_api_vocabulary_rename_recolor_delete(tmp_path):
    client = _client(tmp_path)
    client.post("/api/traces/r1/tags", json={"tag": "typo"})
    client.patch("/api/tags/typo", json={"new_name": "fixed", "color": "#00ff00"})
    facet = client.get("/api/tags").json()["items"][0]
    assert facet["name"] == "fixed" and facet["color"] == "#00ff00"

    client.delete("/api/tags/fixed")
    assert client.get("/api/tags").json()["total"] == 0
    assert client.get("/api/traces/r1").json()["annotations"] == []


def test_api_sessions_and_page(tmp_path):
    client = _client(tmp_path)
    sessions = client.get("/api/sessions").json()
    assert sessions["total"] == 2
    assert {s["session_id"] for s in sessions["items"]} == {"s1", "s2"}
    assert client.get("/sessions").status_code == 200
    assert "Sessions" in client.get("/sessions").text


def test_api_export_returns_full_traces(tmp_path):
    client = _client(tmp_path)
    client.post("/api/traces/tags", json={"run_ids": ["r1"], "tag": "review"})
    res = client.get("/api/traces/export", params={"tag": "review"})
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    body = res.json()
    assert body["count"] == 1
    assert body["items"][0]["id"] == "r1"
    assert body["items"][0]["annotations"][0]["tag"] == "review"


# ---- Postgres backend (opt-in, needs TEST_PG_DSN) -------------------------- #


@pytest.mark.skipif(not os.environ.get("TEST_PG_DSN"), reason="TEST_PG_DSN not set")
def test_postgres_annotations_roundtrip():
    psycopg = pytest.importorskip("psycopg")  # noqa: F841
    from reactifact.tracing import PostgresStore

    store = PostgresStore(os.environ["TEST_PG_DSN"])
    suffix = uuid.uuid4().hex[:8]
    rid = f"pg-tag-{suffix}"
    _seed(store, rid, session_id="s", outcome="completed")

    assert run(store.tag_runs([rid], "bad-prompt", note="n", color="#123456")) == 1
    loaded = run(store.get(rid))
    assert loaded is not None
    assert [(a.tag, a.note, a.color) for a in loaded.annotations] == [
        ("bad-prompt", "n", "#123456")
    ]

    assert run(store.query(tags=["bad-prompt"]))["total"] >= 1
    facets = {f["name"]: f for f in run(store.list_tags())}
    assert facets["bad-prompt"]["count"] >= 1

    run(store.rename_tag("bad-prompt", f"renamed-{suffix}"))
    assert run(store.untag_runs([rid], f"renamed-{suffix}")) == 1
    run(store.delete_tag(f"renamed-{suffix}"))
