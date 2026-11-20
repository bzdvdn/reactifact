"""MapReduce: stable map tasks fanned out, then one debounced reduce."""

from __future__ import annotations

from typing import Any

from reactifact import Context
from reactifact.recipes.map_reduce import map_reduce


async def run_map_reduce_scene() -> dict[str, Any]:
    context = Context()
    result = await map_reduce(
        context,
        ["alpha", "beta", "gamma"],
        map_fn=lambda payload, index: {"text": payload["value"].upper()},
        reduce_fn=lambda rows: {"joined": " | ".join(row["text"] for row in rows)},
        key="tour",
        concurrency=3,
    )
    reduced = result.reduced
    return {
        "tasks": len(result.plan.data.task_ids),
        "results": len(result.results),
        "failures": len(result.failures),
        "reduced": reduced.data.payload.get("joined") if reduced is not None else None,
        "version": result.context.version,
    }
