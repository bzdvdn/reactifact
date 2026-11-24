"""The friendly result object returned by `Pipeline.run()`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast

from ..artifacts import Artifact
from ..budget import RunOutcome
from ..context import Context

if TYPE_CHECKING:
    from ..tracing.models import RunTrace

TArtifact = TypeVar("TArtifact")


@dataclass
class RunResult:
    """What `Pipeline.run()` returns: the context, the count, and helpers.

    `latest(Type)` is the common way to read the answer; `sources(artifact)`
    is the citation query; `hash` is the reproducible context fingerprint.
    """

    context: Context
    runs: int
    outcome: RunOutcome
    #: Trace id, when a tracer/store was configured by `Pipeline(traces=...)`.
    trace_id: str = ""
    #: The store to read the trace back from; not part of the state.
    trace_store: Any = None

    def latest(self, artifact_type: type[TArtifact]) -> Any:
        return self.context.latest(artifact_type)

    def sources(self, artifact: Artifact[Any] | str) -> list[str]:
        return self.context.sources(artifact)

    @property
    def hash(self) -> str:
        from ..audit import context_hash

        return context_hash(self.context)

    async def trace(self, *, span_limit: int = 500) -> RunTrace | None:
        """Read this run's `RunTrace` back from the configured store."""
        if self.trace_store is None or not self.trace_id:
            return None
        return cast(
            "RunTrace | None",
            await self.trace_store.get(self.trace_id, span_limit=span_limit),
        )
