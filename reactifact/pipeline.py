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
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from .artifacts import Artifact
from .budget import Budget
from .context import Context
from .events import Event
from .policy import RetryPolicy
from .providers import LLMProvider
from .resources import RuntimeResources
from .runtime import RunResult, Runtime
from .step import Step, StepGroup
from .step.group import normalize_units
from .tools import Tool, ToolError, ToolOutput

if TYPE_CHECKING:
    from .contracts.report import ContractReport
    from .validate import ValidationReport


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
        trace_sample: Any = None,
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
        self.trace_sample = trace_sample
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

                tracer = Tracer(store=self.trace_store, sample=trace_sample)
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

    def validate(
        self,
        *,
        seeds: Sequence[type | str] = (),
        terminal: Sequence[type | str] = (),
    ) -> ValidationReport:
        """Static contract check for this pipeline (no run, no LLM).

        `seed_type`/`answer_type` are used as the default seeds/terminal when
        set; pass `seeds=`/`terminal=` to override them. This is the same
        `validate_contracts` the CLI and the HTTP adapter use at startup.
        """
        from .validate import validate_contracts

        inferred_seeds: list[type | str] = list(seeds)
        inferred_terminal: list[type | str] = list(terminal)
        if not inferred_seeds and self.seed_type is not None:
            inferred_seeds.append(self.seed_type)
        if not inferred_terminal and self.answer_type is not None:
            inferred_terminal.append(self.answer_type)
        return validate_contracts(
            steps=self.steps,
            groups=self.groups,
            seeds=inferred_seeds,
            terminal=inferred_terminal,
        )

    def describe(self) -> ContractReport:
        """The static contract plus seed/answer JSON Schemas.

        The transport-neutral "OpenAPI": the CLI prints it, the HTTP adapter
        serves it as `/api/contracts`, and MCP/tool adapters read the same
        models. It never runs the pipeline.
        """
        from .contracts.report import ContractReport, describe_contracts

        groups = normalize_units(steps=self.steps, groups=self.groups)
        base = describe_contracts(groups)
        return ContractReport(
            groups=base.groups,
            seed_schema=(
                self.seed_type.model_json_schema()
                if self.seed_type is not None
                else None
            ),
            answer_schema=(
                self.answer_type.model_json_schema()
                if self.answer_type is not None
                else None
            ),
        )

    def router(self, **kwargs: Any) -> Any:
        """A FastAPI `APIRouter` with `/run`, `/stream` and `/health`.

        Needs `seed_type=` and `answer_type=`. FastAPI is imported lazily, so
        the core package does not depend on it; install `reactifact[web]`.
        """
        from .web import create_pipeline_router

        return create_pipeline_router(self, **kwargs)

    def http(self, **kwargs: Any) -> Any:
        """A ready FastAPI app: run/stream routes, `/docs`, `/contracts`, traces.

        The web adapter, not the core: `Pipeline` stays usable from a CLI,
        daemon, queue or MCP without FastAPI installed.
        """
        from .web import create_http_app

        return create_http_app(self, **kwargs)

    def as_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        destructive: bool = False,
        retry: RetryPolicy | None = None,
        timeout: float | None = None,
    ) -> Tool:
        """Expose this pipeline as a `Tool` (MCP, tool loops, agent-as-tool).

        The tool schema is `seed_type`'s JSON Schema and the result is the
        latest `answer_type`; `create_mcp_server([pipeline.as_tool()])` is the
        MCP adapter. Requires `seed_type=` and `answer_type=`.
        """
        if self.seed_type is None or self.answer_type is None:
            raise TypeError("Pipeline.as_tool requires seed_type= and answer_type=")
        return _PipelineTool(
            self,
            name=name,
            description=description,
            destructive=destructive,
            retry=retry,
            timeout=timeout,
        )

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

    def answer(
        self,
        seed: Artifact[Any] | Any,
        *,
        max_iterations: int = 100,
        budget: Budget | None = None,
        request: Mapping[str, Any] | None = None,
    ) -> BaseModel:
        """Run once and return the typed `answer_type` artifact.

        The `Pipeline` equivalent of `Chat.answer()`; requires `answer_type=...`.
        """
        if self.answer_type is None:
            raise TypeError("Pipeline.answer requires answer_type=...")
        result = self.run(
            seed, max_iterations=max_iterations, budget=budget, request=request
        )
        artifact = result.context.latest(self.answer_type)
        if artifact is None:
            raise LookupError(
                f"Pipeline.answer: no {self.answer_type.__name__} was produced"
            )
        return artifact.data

    async def aanswer(
        self,
        seed: Artifact[Any] | Any,
        *,
        max_iterations: int = 100,
        budget: Budget | None = None,
        request: Mapping[str, Any] | None = None,
    ) -> BaseModel:
        """Async form of `answer()`."""
        if self.answer_type is None:
            raise TypeError("Pipeline.answer requires answer_type=...")
        result = await self.arun(
            seed, max_iterations=max_iterations, budget=budget, request=request
        )
        artifact = result.context.latest(self.answer_type)
        if artifact is None:
            raise LookupError(
                f"Pipeline.answer: no {self.answer_type.__name__} was produced"
            )
        return artifact.data

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
            trace_id=runtime.run_id,
            trace_store=self.trace_store,
        )


class _PipelineTool(Tool):
    """A `Tool` view of a typed Pipeline (schema from seed/answer models)."""

    def __init__(
        self,
        pipeline: Pipeline,
        *,
        name: str | None,
        description: str | None,
        destructive: bool,
        retry: RetryPolicy | None,
        timeout: float | None,
    ):
        assert pipeline.seed_type is not None and pipeline.answer_type is not None
        self._pipeline = pipeline
        self._seed_type = pipeline.seed_type
        self._answer_type = pipeline.answer_type
        self.name = name or "pipeline"
        self.description = description or (
            f"Run the {self._seed_type.__name__} -> "
            f"{self._answer_type.__name__} pipeline"
        )
        self.destructive = destructive
        self.schema = self._seed_type.model_json_schema()
        self.retry = retry
        self.timeout = timeout

    async def execute(self, args: dict[str, Any]) -> ToolOutput:
        seed = self._seed_type.model_validate(args)
        result = await self._pipeline.arun(seed)
        artifact = result.context.latest(self._answer_type)
        if artifact is None:
            return ToolOutput.failed(
                ToolError(
                    code="no_answer",
                    message=(
                        f"pipeline completed without a "
                        f"{self._answer_type.__name__} artifact"
                    ),
                )
            )
        data = artifact.data
        text = getattr(data, "text", "")
        return ToolOutput(
            data=data.model_dump(mode="json"),
            text=text if isinstance(text, str) else "",
        )


__all__ = ["Pipeline"]
