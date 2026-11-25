"""MapReduce: fan out a typed task per item, then reduce the results (§24).

The recipe owns the reactive wiring so the domain supplies two callbacks:

    result = await map_reduce(
        context,
        items=docs,
        map_fn=lambda doc, i: summarize(doc),
        reduce_fn=lambda rows: merge(rows),
        key="summary",
    )

Each item becomes a stable `MapTask` (`map-task:{key}:{i}`), a `MapWorker` turns
it into a `MapResult`, and a debounced `ReduceStep` runs once all results for
the key exist. The child run has its own commit DAG (use `result.context` for
provenance, or merge it into the parent); the parent tracer/budget are reused.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..artifacts import Artifact
from ..budget import RunStats
from ..consume import Consume
from ..context import Context
from ..effects import Effects
from ..runtime import Runtime
from ..step import Step, create_step_group

MapFn = Any  # (item, index) -> model/mapping, sync or async
ReduceFn = Any  # (successful_payloads) -> model/mapping, sync or async


class MapTask(BaseModel):
    key: str
    index: int
    payload: dict[str, Any] = Field(default_factory=dict)


class MapResult(BaseModel):
    key: str
    index: int
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class MapPlan(BaseModel):
    key: str
    total: int
    task_ids: list[str] = Field(default_factory=list)


class MapReduced(BaseModel):
    key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    failures: int = 0


@dataclass
class MapReduceResult:
    context: Context
    plan: Artifact[MapPlan]
    results: list[Artifact[MapResult]] = field(default_factory=list)
    failures: list[Artifact[MapResult]] = field(default_factory=list)
    reduced: Artifact[MapReduced] | None = None
    run_id: str = ""
    parent_run_id: str = ""
    stats: RunStats | None = None


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": value}


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class MapWorker(Step):
    consumes = (MapTask,)
    produces = (MapResult,)

    def __init__(self, fn: MapFn, *, on_error: Literal["fail_fast", "collect"]):
        super().__init__()
        self.fn = fn
        self.on_error = on_error

    async def run(self, task: MapTask, effects: Effects) -> None:
        try:
            value = await _maybe_await(self.fn(task.payload, task.index))
            payload = _payload(value)
            error = None
        except Exception as exc:
            if self.on_error == "fail_fast":
                raise
            payload = {}
            error = f"{type(exc).__name__}: {exc}"
        effects.create(
            MapResult(
                key=task.key,
                index=task.index,
                payload=payload,
                error=error,
            ),
            id=f"map-result:{task.key}:{task.index}",
        )


class ReduceStep(Step):
    consumes = (Consume(MapResult, debounce=True),)
    produces = (MapReduced,)

    def __init__(self, fn: ReduceFn, *, plan_id: str, key: str):
        super().__init__()
        self.fn = fn
        self.plan_id = plan_id
        self.key = key

    async def run(self, context: Context, result: MapResult, effects: Effects) -> None:
        plan = context.get(self.plan_id)
        if plan is None or not isinstance(plan.data, MapPlan):
            return
        reduced_id = f"map-reduced:{self.key}"
        if context.get(reduced_id) is not None:
            return
        results = [
            artifact
            for artifact in context.list_artifacts(MapResult)
            if artifact.data.key == self.key
        ]
        results.sort(key=lambda artifact: artifact.data.index)
        if len(results) < plan.data.total:
            return
        successful = [
            artifact.data.payload for artifact in results if not artifact.data.error
        ]
        value = await _maybe_await(self.fn(successful))
        effects.create(
            MapReduced(
                key=self.key,
                payload=_payload(value),
                failures=len(results) - len(successful),
            ),
            id=reduced_id,
        )


async def map_reduce(
    parent: Context,
    items: Sequence[Any],
    *,
    map_fn: MapFn,
    reduce_fn: ReduceFn,
    key: str = "map",
    key_fn: Any | None = None,
    concurrency: int | None = None,
    on_error: Literal["fail_fast", "collect"] = "fail_fast",
    inherit_trace: bool = True,
    inherit_budget: bool = True,
    max_iterations: int = 100,
    name: str = "map-reduce",
) -> MapReduceResult:
    """Fans `items` out through `map_fn`, then reduces successful payloads.

    `key_fn(item, index)` derives each task's stable key (default: its index);
    `key` namespaces the plan and the reduced artifact (`map-plan:{key}`,
    `map-reduced:{key}`). `on_error="collect"` turns a failing item into a
    `MapResult(error=...)` and still reduces the rest; `"fail_fast"` propagates.
    The child Context is isolated (same resources), so use `result.context` for
    provenance or merge it into the parent yourself.
    """
    items = list(items)
    child = Context(resources=parent.resources)
    task_ids: list[str] = []
    for index, item in enumerate(items):
        item_key = str(key_fn(item, index)) if key_fn is not None else str(index)
        task = child.create(
            MapTask(key=key, index=index, payload=_payload(item)),
            id=f"map-task:{key}:{index}:{item_key}",
        )
        task_ids.append(task.id)
    plan = child.create(
        MapPlan(key=key, total=len(items), task_ids=task_ids),
        id=f"map-plan:{key}",
    )

    if not items:
        reduced = child.create(
            MapReduced(key=key, payload=_payload(await _maybe_await(reduce_fn([])))),
            id=f"map-reduced:{key}",
        )
        return MapReduceResult(
            context=child,
            plan=plan,
            reduced=reduced,
            parent_run_id=parent.resources.run_id,
        )

    child_runtime = Runtime(
        child,
        groups=[
            create_step_group(
                f"{name}-map", steps=[MapWorker(map_fn, on_error=on_error)]
            ),
            create_step_group(
                f"{name}-reduce",
                steps=[ReduceStep(reduce_fn, plan_id=plan.id, key=key)],
            ),
        ],
        max_concurrency=concurrency,
        budget=parent.resources.budget if inherit_budget else None,
        tracer=parent.resources.tracer if inherit_trace else None,
        trace_scope=parent.resources.trace_scope,
    )
    request: dict[str, Any] | None = None
    if parent.resources.run_id:
        request = {
            "trace_metadata": {
                "parent_run_id": parent.resources.run_id,
                "map_reduce": key,
            }
        }
        if parent.resources.trace_scope:
            request["workspace_id"] = parent.resources.trace_scope

    await child_runtime.arun(max_iterations=max_iterations, request=request)

    results = sorted(
        child.list_artifacts(MapResult),
        key=lambda artifact: artifact.data.index,
    )
    return MapReduceResult(
        context=child,
        plan=plan,
        results=results,
        failures=[artifact for artifact in results if artifact.data.error],
        reduced=child.get(f"map-reduced:{key}"),
        run_id=child_runtime.run_id,
        parent_run_id=parent.resources.run_id,
        stats=child_runtime.last_stats,
    )


__all__ = [
    "MapPlan",
    "MapReduceResult",
    "MapReduced",
    "MapResult",
    "MapTask",
    "map_reduce",
]
