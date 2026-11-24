"""`Pipeline` — the FastAPI-style facade over Context + Runtime.

    pipeline = Pipeline(steps=[plan, search, answer])
    result = pipeline.run(Question(text="..."))
    result.latest(Answer)

`Pipeline` is a thin facade, not a second engine: each run builds a fresh
`Context` and `Runtime` from the same steps/resources. The low-level objects
stay public for branch/merge, replay and tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from .artifacts import Artifact
from .budget import Budget
from .context import Context
from .events import Event
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
        budget: Budget | None = None,
        tracer: Any = None,
        max_concurrency: int | None = None,
        isolate_errors: bool = False,
        on_agent_error: Callable[[StepGroup, Event, BaseException], None] | None = None,
        seed_type: type[BaseModel] | None = None,
        seed_field: str = "text",
        answer_type: type[BaseModel] | None = None,
    ):
        if steps is None and groups is None:
            raise TypeError("Pipeline requires steps= and/or groups=")
        self.steps = list(steps) if steps is not None else None
        self.groups = list(groups) if groups is not None else None
        self.resources = resources if resources is not None else RuntimeResources()
        self.budget = budget
        self.tracer = tracer
        self.max_concurrency = max_concurrency
        self.isolate_errors = isolate_errors
        self.on_agent_error = on_agent_error
        #: Optional request/response models for `reactifact run` and tooling:
        #: `seed_type` builds the seed from `--text`, `answer_type` is the
        #: artifact to print by default.
        self.seed_type = seed_type
        self.seed_field = seed_field
        self.answer_type = answer_type
        #: Context of the most recent run (also carried by its `RunResult`).
        self.last_context: Context | None = None
        #: `RunResult` of the most recent `run`/`arun`/`astream`.
        self.last_result: RunResult | None = None

    def _prepare(self, seed: Artifact[Any] | Any) -> tuple[Context, Runtime]:
        context = Context(resources=self.resources)
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
        result = RunResult(context, runs, runtime.outcome)
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
        result = RunResult(context, runs, runtime.outcome)
        self.last_result = result
        return result

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
