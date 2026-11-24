"""In-memory clients for tests (FastAPI's `TestClient` analogue)."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..pipeline import Pipeline
from ..resources import RuntimeResources
from ..runtime import RunResult

if TYPE_CHECKING:
    from ..chat import Chat, ChatEvent


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


class TestChat:
    """A copy of a `Chat` with a fresh in-memory store and trace store.

    `chat = Chat(...); client = chat.test_client()` — turns in the client never
    touch the production sessions or traces. Pass a fake LLM when building the
    `Chat` (`llm=FakeLLM(...)`) or make `resources` a callable.
    """

    def __init__(self, chat: Chat, *, traces: bool = True):
        from ..checkpoints import InMemoryKVBackend
        from ..session import SessionStore

        if chat.lifespan is not None:
            raise RuntimeError(
                "test_client does not run a Chat lifespan; enter the chat with "
                "`async with chat:` instead"
            )
        self.chat = copy.copy(chat)
        self.chat.assistant = copy.copy(chat.assistant)
        self.chat.assistant.store = SessionStore(InMemoryKVBackend())
        self.traces: Any = None
        if traces:
            from ..tracing import Tracer, TraceStore

            self.traces = TraceStore(":memory:")
            self.chat.assistant._tracer = Tracer(
                store=self.traces, sample=chat.trace_sample
            )

    def override(self, kind: type[Any], instance: Any) -> TestChat:
        """Replace a typed resource in this client, like FastAPI's overrides."""
        resources = self.chat.assistant._resources
        if isinstance(resources, RuntimeResources):
            resolved: Any = resources.with_overrides({kind: instance})
        elif callable(resources):
            original = resources

            def resolve(*args: Any, **kwargs: Any) -> RuntimeResources:
                return original(*args, **kwargs).with_overrides({kind: instance})

            resolved = resolve
        else:
            resolved = RuntimeResources().with_overrides({kind: instance})
        self.chat.assistant._resources = resolved
        self.chat.resources = resolved
        return self

    async def invoke(
        self, text: str, session_id: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        return await self.chat.invoke(text, session_id=session_id, **kwargs)

    async def answer(self, text: str, session_id: str = "", **kwargs: Any) -> Any:
        return await self.chat.answer(text, session_id=session_id, **kwargs)

    async def stream(
        self, text: str, session_id: str = "", **kwargs: Any
    ) -> AsyncIterator[ChatEvent]:
        async for event in self.chat.stream(text, session_id=session_id, **kwargs):
            yield event

    async def history(self, session_id: str = "") -> dict[str, Any]:
        return await self.chat.history(session_id)

    @property
    def store(self) -> Any:
        return self.chat.store

    def close(self) -> None:
        if self.traces is not None:
            self.traces.close()

    def __enter__(self) -> TestChat:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


__all__ = ["TestChat", "TestPipeline"]
