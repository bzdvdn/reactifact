"""The friendly result object returned by `Pipeline.run()`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from ..artifacts import Artifact
from ..budget import RunOutcome
from ..context import Context

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

    def latest(self, artifact_type: type[TArtifact]) -> Any:
        return self.context.latest(artifact_type)

    def sources(self, artifact: Artifact[Any] | str) -> list[str]:
        return self.context.sources(artifact)

    @property
    def hash(self) -> str:
        from ..audit import context_hash

        return context_hash(self.context)
