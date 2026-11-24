"""`Pipeline` — the FastAPI-style facade over Context + Runtime.

    pipeline = Pipeline(steps=[plan, search, answer])
    result = pipeline.run(Question(text="..."))
    result.latest(Answer)

`Pipeline` is a thin facade, not a second engine: each run builds a fresh
`Context` and `Runtime` from the same steps/resources. The low-level objects
stay public for branch/merge, replay and tests.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .artifacts import Artifact
from .budget import Budget
from .context import Context
from .events import Event
from .providers import LLMProvider
from .resources import RuntimeResources
from .runtime import RunResult, Runtime
from .step import Step, StepGroup


class Pipeline:
    """A reusable agent pipeline: steps + resources + defaults.

    `steps=` and `groups=` are the same keywords `Runtime` accepts. `run()` /
    `arun()` seed one artifact and return a `RunResult`; `astream()` streams
    progress events and leaves the final `last_result` behind.
    """

    def __init__(
        self,
        steps: Sequence[Step[Any]] | None = None,
        *,
        groups: Sequence[StepGroup] | None = None,
        resources: RuntimeResources | None = None,
        llm: LLMProvider | None = None,
        budget: Budget | None = None,
        tracer: Any = None,
        max_concurrency: int | None = None,
        isolate_errors: bool = False,
        on_agent_error: Callable[[StepGroup, Event, BaseException], None] | None = None,
        seed_type: type[BaseModel] | None = None,
        seed_field: str = "text",
        answer_type: type[BaseModel] | None = None,
        lifespan: Callable[[], AbstractAsyncContextManager[RuntimeResources]]
        | None = None,
        traces: bool | str | Path | Any | None = None,
    ):
        if steps is None and groups is None:
            raise TypeError("Pipeline requires steps= and/or groups=")
        self.steps = list(steps) if steps is not None else None
        self.groups = list(groups) if groups is not None else None
        self.resources = resources if resources is not None else RuntimeResources()
        #: The pipeline's one default provider. `llm=` is a shortcut for
        #: `RuntimeResources(llm=...)`; pass a router/fallback object here if a
        #: domain needs several providers behind one interface.
        self.llm = llm
        self.budget = budget
        self.trace_store: Any = None
        if traces:
            from .tracing import TraceStore

            self.trace_store = (
                TraceStore(str(traces))
                if isinstance(traces, (str, Path))
                else traces
                if not isinstance(traces, bool)
                else TraceStore(":memory:")
            )
            if tracer is None:
                from .tracing import Tracer

                tracer = Tracer(store=self.trace_store)
        self.tracer = tracer
        self.max_concurrency = max_concurrency
        self.isolate_errors = isolate_errors
        self.on_agent_error = on_agent_error
        self._overrides: dict[type[Any], Any] = {}
        #: Optional request/response models for `reactifact run` and tooling:
        #: `seed_type` builds the seed from `--text`, `answer_type` is the
        #: artifact to print by default.
        self.seed_type = seed_type
        self.seed_field = seed_field
        self.answer_type = answer_type
        #: Optional async context manager yielding `RuntimeResources`; enter
        #: the pipeline with `async with pipeline:` so clients/indexes are
        #: created once and closed deterministically.
        self.lifespan = lifespan
        self._lifespan_cm: AbstractAsyncContextManager[RuntimeResources] | None = None
        #: Context of the most recent run (also carried by its `RunResult`).
        self.last_context: Context | None = None
        #: `RunResult` of the most recent `run`/`arun`/`astream`.
        self.last_result: RunResult | None = None

    async def __aenter__(self) -> Pipeline:
        if self.lifespan is not None:
            self._lifespan_cm = self.lifespan()
            self.resources = await self._lifespan_cm.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._lifespan_cm is not None:
            await self._lifespan_cm.__aexit__(exc_type, exc, tb)
            self._lifespan_cm = None

    def override(self, kind: type[Any], instance: Any) -> Pipeline:
        """Replace a typed resource for this pipeline (tests, local fakes).

        Same role as FastAPI's `dependency_overrides`: a step annotated
        `llm: LLMProvider` receives `instance` without rebuilding resources.
        """
        self._overrides[kind] = instance
        return self

    def clear_overrides(self) -> Pipeline:
        """Drop every `override` and fall back to the configured resources."""
        self._overrides.clear()
        return self

    def _prepare(self, seed: Artifact[Any] | Any) -> tuple[Context, Runtime]:
        if self.lifespan is not None and self._lifespan_cm is None:
            raise RuntimeError(
                "this Pipeline has a lifespan; use `async with pipeline:` and "
                "`await pipeline.arun(...)`"
            )
        resources = self.resources
        if self.llm is not None:
            resources = copy.copy(resources)
            resources.llm = self.llm
        if self._overrides:
            resources = resources.with_overrides(self._overrides)
        context = Context(resources=resources)
        if isinstance(seed, Artifact):
            if context.get(seed.id) is None:
                context.create(seed.data, id=seed.id)
        else:
            context.create(seed)
        runtime = Runtime(
            context,
            steps=self.steps,
            groups=self.groups,
            budget=self.budget,
            tracer=self.tracer,
            max_concurrency=self.max_concurrency,
            isolate_errors=self.isolate_errors,
            on_agent_error=self.on_agent_error,
        )
        self.last_context = context
        return context, runtime

    def run(
        self,
        seed: Artifact[Any] | Any,
        *,
        max_iterations: int = 100,
        budget: Budget | None = None,
        request: Mapping[str, Any] | None = None,
    ) -> RunResult:
        """Seed one artifact, run to completion, return a `RunResult`."""
        context, runtime = self._prepare(seed)
        runs = runtime.run(
            max_iterations=max_iterations,
            budget=budget or self.budget,
            request=request,
        )
        result = RunResult(
            context,
            runs,
            runtime.outcome,
            trace_id=runtime.run_id,
            trace_store=self.trace_store,
        )
        self.last_result = result
        return result

    async def arun(
        self,
        seed: Artifact[Any] | Any,
        *,
        max_iterations: int = 100,
        budget: Budget | None = None,
        request: Mapping[str, Any] | None = None,
    ) -> RunResult:
        """Async form of `run()`."""
        context, runtime = self._prepare(seed)
        runs = await runtime.arun(
            max_iterations=max_iterations,
            budget=budget or self.budget,
            request=request,
        )
        result = RunResult(
            context,
            runs,
            runtime.outcome,
            trace_id=runtime.run_id,
            trace_store=self.trace_store,
        )
        self.last_result = result
        return result

    def close(self) -> None:
        """Close the trace store opened by `traces=` (no-op otherwise)."""
        if self.trace_store is not None and hasattr(self.trace_store, "close"):
            self.trace_store.close()

    def test_client(self, *, traces: bool = True) -> Any:
        """An in-memory `Pipeline` for tests (fresh overrides, trace store)."""
        from .testing.client import TestPipeline

        return TestPipeline(self, traces=traces)

    async def astream(
        self,
        seed: Artifact[Any] | Any,
        *,
        max_iterations: int = 1000,
        budget: Budget | None = None,
        request: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        """Yield progress events; `last_result` is filled at the end."""
        context, runtime = self._prepare(seed)
        async for event in runtime.astream(
            budget=budget or self.budget,
            max_iterations=max_iterations,
            request=request,
        ):
            yield event
        self.last_result = RunResult(
            context,
            runtime.last_stats.runs if runtime.last_stats else 0,
            runtime.outcome,
        )


__all__ = ["Pipeline"]
