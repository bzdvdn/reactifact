"""`PipelineRunner`: runtime resources and execution around a definition."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..agent import Agent, Step
from ..artifacts import Artifact
from ..budget import Budget
from ..context import Context
from ..invocation import Invocation
from ..policy import RetryPolicy
from ..resources import RuntimeResources
from ..runtime import RunResult, Runtime
from ..tools import Tool, ToolError, ToolOutput
from .config import RunConfig
from .definition import PipelineDefinition

if TYPE_CHECKING:
    from ..contracts.report import ContractReport
    from ..validate import ValidationReport


class PipelineRunner:
    """Executes a `Pipeline` definition with runtime resources and defaults.

    `Pipeline` owns the structure (agents/routes/steps/seed/answer); the runner
    owns resources, budget, tracer, concurrency and overrides. Pass a
    `RunConfig` for resources, tracing, budget, concurrency and lifespan.
    `run()`/`arun()` seed one artifact and return a `RunResult`; `astream()`
    streams progress events and leaves the final `last_result` behind.
    """

    def __init__(
        self,
        pipeline: PipelineDefinition,
        config: RunConfig | None = None,
    ) -> None:
        base = config or RunConfig()
        self.pipeline = pipeline
        self.config = config
        self.schedule = base.schedule or pipeline.schedule()
        self.resources = base.resources
        self.llm = base.llm
        self.budget = base.budget
        self.max_concurrency = base.max_concurrency
        self.isolate_errors = base.isolate_errors or False
        self.on_agent_error = base.on_agent_error
        self.lifespan = base.lifespan
        self.trace_sample = base.trace_sample
        self.trace_store: Any = None
        self.tracer = base.tracer
        if base.traces:
            from ..tracing import TraceStore

            self.trace_store = (
                TraceStore(str(base.traces))
                if isinstance(base.traces, (str, Path))
                else base.traces
                if not isinstance(base.traces, bool)
                else TraceStore(":memory:")
            )
            if self.tracer is None:
                from ..tracing import Tracer

                self.tracer = Tracer(
                    store=self.trace_store,
                    sample=self.trace_sample,
                )
        self._overrides: dict[type[Any], Any] = {}
        self._lifespan_cm: AbstractAsyncContextManager[RuntimeResources] | None = None
        self.last_context: Context | None = None
        self.last_result: RunResult | None = None

    @property
    def steps(self) -> Sequence[Step[Any]] | None:
        return self.pipeline.steps

    @property
    def agents(self) -> list[Agent]:
        return self.pipeline.agents

    @property
    def name(self) -> str:
        return self.pipeline.name

    @property
    def seed_type(self) -> type[BaseModel] | None:
        return self.pipeline.seed_type

    @property
    def answer_type(self) -> type[BaseModel] | None:
        return self.pipeline.answer_type

    async def __aenter__(self) -> PipelineRunner:
        if self.lifespan is not None:
            self._lifespan_cm = self.lifespan()
            self.resources = await self._lifespan_cm.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._lifespan_cm is not None:
            await self._lifespan_cm.__aexit__(exc_type, exc, tb)
            self._lifespan_cm = None

    def override(self, kind: type[Any], instance: Any) -> PipelineRunner:
        """Replace a typed resource for this runner (tests, local fakes)."""
        self._overrides[kind] = instance
        return self

    def clear_overrides(self) -> PipelineRunner:
        """Drop every `override` and fall back to the configured resources."""
        self._overrides.clear()
        return self

    def validate(
        self,
        *,
        seeds: Sequence[type | str] = (),
        terminal: Sequence[type | str] = (),
    ) -> ValidationReport:
        return self.pipeline.validate(seeds=seeds, terminal=terminal)

    def describe(self) -> ContractReport:
        return self.pipeline.describe()

    def mermaid(self, *, with_stages: bool = True) -> str:
        return self.pipeline.mermaid(with_stages=with_stages)

    def router(self, **kwargs: Any) -> Any:
        """A FastAPI `APIRouter` with `/run`, `/stream` and `/health`."""
        from ..web import create_pipeline_router

        return create_pipeline_router(self, **kwargs)

    def http(self, **kwargs: Any) -> Any:
        """A ready FastAPI app around this runner's pipeline."""
        from ..web import create_http_app

        return create_http_app(self, **kwargs)

    def test_client(self, *, traces: bool = True) -> Any:
        """An isolated copy of this runner with an in-memory trace store."""
        from ..testing.client import TestPipeline

        return TestPipeline(self, traces=traces)

    def as_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        destructive: bool = False,
        retry: RetryPolicy | None = None,
        timeout: float | None = None,
    ) -> Tool:
        """Expose this runner as a `Tool` (MCP, tool loops, agent-as-tool)."""
        if self.seed_type is None or self.answer_type is None:
            raise TypeError(
                "PipelineRunner.as_tool requires seed_type= and answer_type="
            )
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
                "this PipelineRunner has a lifespan; use `async with runner:` "
                "and `await runner.arun(...)`"
            )
        resources = self.resources or RuntimeResources()
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
            agents=self.pipeline.agents,
            budget=self.budget,
            tracer=self.tracer,
            max_concurrency=self.max_concurrency,
            isolate_errors=self.isolate_errors,
            on_agent_error=self.on_agent_error,
            schedule=self.schedule,
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
        invocation: Invocation | None = None,
    ) -> RunResult:
        """Seed one artifact, run to completion, return a `RunResult`."""
        context, runtime = self._prepare(seed)
        runs = runtime.run(
            max_iterations=max_iterations,
            budget=budget or self.budget,
            request=request,
            invocation=invocation,
        )
        result = RunResult(
            context,
            runs,
            runtime.outcome,
            trace_id=runtime.run_id,
            trace_store=self.trace_store,
            invocation=runtime.invocation,
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
        invocation: Invocation | None = None,
    ) -> RunResult:
        """Async form of `run()`."""
        context, runtime = self._prepare(seed)
        runs = await runtime.arun(
            max_iterations=max_iterations,
            budget=budget or self.budget,
            request=request,
            invocation=invocation,
        )
        result = RunResult(
            context,
            runs,
            runtime.outcome,
            trace_id=runtime.run_id,
            trace_store=self.trace_store,
            invocation=runtime.invocation,
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
        invocation: Invocation | None = None,
    ) -> BaseModel:
        """Run once and return the typed `answer_type` artifact."""
        if self.answer_type is None:
            raise TypeError("PipelineRunner.answer requires answer_type=...")
        result = self.run(
            seed,
            max_iterations=max_iterations,
            budget=budget,
            request=request,
            invocation=invocation,
        )
        artifact = result.context.latest(self.answer_type)
        if artifact is None:
            raise LookupError(
                f"PipelineRunner.answer: no {self.answer_type.__name__} was produced"
            )
        return artifact.data

    async def aanswer(
        self,
        seed: Artifact[Any] | Any,
        *,
        max_iterations: int = 100,
        budget: Budget | None = None,
        request: Mapping[str, Any] | None = None,
        invocation: Invocation | None = None,
    ) -> BaseModel:
        """Async form of `answer()`."""
        if self.answer_type is None:
            raise TypeError("PipelineRunner.aanswer requires answer_type=...")
        result = await self.arun(
            seed,
            max_iterations=max_iterations,
            budget=budget,
            request=request,
            invocation=invocation,
        )
        artifact = result.context.latest(self.answer_type)
        if artifact is None:
            raise LookupError(
                f"PipelineRunner.aanswer: no {self.answer_type.__name__} was produced"
            )
        return artifact.data

    async def astream(
        self,
        seed: Artifact[Any] | Any,
        *,
        max_iterations: int = 1000,
        budget: Budget | None = None,
        request: Mapping[str, Any] | None = None,
        invocation: Invocation | None = None,
    ) -> AsyncIterator[Any]:
        """Yield progress events; `last_result` is filled at the end."""
        context, runtime = self._prepare(seed)
        async for event in runtime.astream(
            budget=budget or self.budget,
            max_iterations=max_iterations,
            request=request,
            invocation=invocation,
        ):
            yield event
        self.last_result = RunResult(
            context,
            runtime.last_stats.runs if runtime.last_stats else 0,
            runtime.outcome,
            trace_id=runtime.run_id,
            trace_store=self.trace_store,
            invocation=runtime.invocation,
        )

    def close(self) -> None:
        """Close the trace store opened by `traces=` (no-op otherwise)."""
        if self.trace_store is not None and hasattr(self.trace_store, "close"):
            self.trace_store.close()


class _PipelineTool(Tool):
    """A `Tool` view of a typed Pipeline (schema from seed/answer models)."""

    def __init__(
        self,
        pipeline: PipelineRunner,
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


__all__ = ["PipelineRunner"]
