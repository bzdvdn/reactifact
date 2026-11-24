"""An in-memory `Pipeline` for tests (FastAPI's `TestClient` analogue)."""

from __future__ import annotations

import copy
from typing import Any

from ..pipeline import Pipeline
from ..runtime import RunResult


class TestPipeline:
    """A copy of a `Pipeline` with isolated overrides and a trace store.

    Nothing touches the network by itself: pass a fake LLM through
    `override(LLMProvider, FakeLLM())` (or the pipeline's `llm=`). The trace
    store is SQLite `:memory:` so assertions can inspect spans after a run.
    """

    def __init__(self, pipeline: Pipeline, *, traces: bool = True):
        if pipeline.lifespan is not None:
            raise RuntimeError(
                "test_client does not run a Pipeline lifespan; enter the "
                "pipeline with `async with pipeline:` instead"
            )
        self.pipeline = copy.copy(pipeline)
        self.pipeline._overrides = dict(pipeline._overrides)
        self.traces: Any = None
        if traces:
            from ..tracing import Tracer, TraceStore

            self.traces = TraceStore(":memory:")
            self.pipeline.tracer = Tracer(store=self.traces)

    def override(self, kind: type[Any], instance: Any) -> TestPipeline:
        self.pipeline.override(kind, instance)
        return self

    def run(self, seed: Any, **kwargs: Any) -> RunResult:
        return self.pipeline.run(seed, **kwargs)

    async def arun(self, seed: Any, **kwargs: Any) -> RunResult:
        return await self.pipeline.arun(seed, **kwargs)

    @property
    def last_result(self) -> RunResult | None:
        return self.pipeline.last_result

    @property
    def last_context(self) -> Any:
        return self.pipeline.last_context

    def close(self) -> None:
        if self.traces is not None:
            self.traces.close()

    def __enter__(self) -> TestPipeline:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


__all__ = ["TestPipeline"]
