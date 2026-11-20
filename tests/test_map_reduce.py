"""MapReduce recipe: stable tasks, debounced reduce, failures, empty input."""

from __future__ import annotations

import asyncio

import pytest
from reactifact import Context
from reactifact.recipes.map_reduce import map_reduce


def test_map_reduce_fans_out_and_reduces_in_order():
    async def go():
        context = Context()
        result = await map_reduce(
            context,
            ["a", "b", "c"],
            map_fn=lambda payload, index: {"text": payload["value"].upper()},
            reduce_fn=lambda rows: {"joined": ",".join(row["text"] for row in rows)},
            key="upper",
            concurrency=3,
        )
        return result

    result = asyncio.run(go())

    assert [item.data.index for item in result.results] == [0, 1, 2]
    assert all(item.data.error is None for item in result.results)
    assert result.failures == []
    assert result.reduced is not None
    assert result.reduced.id == "map-reduced:upper"
    assert result.reduced.data.payload == {"joined": "A,B,C"}
    assert result.reduced.data.failures == 0
    assert result.context.version >= 2


def test_map_reduce_collects_failures_and_reduces_the_rest():
    def map_item(payload, index):
        if payload["value"] == "bad":
            raise ValueError("boom")
        return {"text": payload["value"]}

    async def go():
        return await map_reduce(
            Context(),
            ["ok", "bad", "fine"],
            map_fn=map_item,
            reduce_fn=lambda rows: {"joined": ",".join(row["text"] for row in rows)},
            key="collect",
            on_error="collect",
        )

    result = asyncio.run(go())

    assert len(result.results) == 3
    assert len(result.failures) == 1
    assert result.failures[0].data.index == 1
    assert "ValueError" in (result.failures[0].data.error or "")
    assert result.reduced is not None
    assert result.reduced.data.payload == {"joined": "ok,fine"}
    assert result.reduced.data.failures == 1


def test_map_reduce_fail_fast_propagates():
    def map_item(payload, index):
        raise RuntimeError("stop")

    async def go():
        await map_reduce(
            Context(),
            [1],
            map_fn=map_item,
            reduce_fn=lambda rows: {"count": len(rows)},
            key="fail",
            on_error="fail_fast",
        )

    with pytest.raises(RuntimeError, match="stop"):
        asyncio.run(go())


def test_map_reduce_key_fn_and_task_ids_are_stable():
    async def go():
        return await map_reduce(
            Context(),
            [{"id": "x"}, {"id": "y"}],
            map_fn=lambda payload, index: {"id": payload["id"]},
            reduce_fn=lambda rows: {"count": len(rows)},
            key="stable",
            key_fn=lambda item, index: item["id"],
        )

    result = asyncio.run(go())

    assert result.plan.data.task_ids == [
        "map-task:stable:0:x",
        "map-task:stable:1:y",
    ]
    assert result.reduced is not None


def test_map_reduce_empty_input_reduces_once():
    async def go():
        return await map_reduce(
            Context(),
            [],
            map_fn=lambda payload, index: payload,
            reduce_fn=lambda rows: {"count": len(rows)},
            key="empty",
        )

    result = asyncio.run(go())

    assert result.results == []
    assert result.reduced is not None
    assert result.reduced.data.payload == {"count": 0}
