"""Context indices: producing commits, dependents and staleness (§43-§44)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..artifacts import Artifact
from ..commit import Commit

if TYPE_CHECKING:
    from .context import Context


def producing_commit(context: Context, artifact_id: str) -> Commit | None:
    """The last commit that wrote the artifact (create or update)."""
    return context._log.producing_commit(artifact_id)


def dependents_of(context: Context, artifact_id: str) -> list[Artifact[Any]]:
    """Artifacts whose producing commit read `artifact_id` at an older version."""
    current = context._artifacts.get(artifact_id)
    if current is None:
        return []
    dependents: list[Artifact[Any]] = []
    for aid in context._log.dependents_of(artifact_id):
        artifact = context._artifacts.get(aid)
        if artifact is None:
            continue
        commit = producing_commit(context, aid)
        if commit is None:
            continue
        for read in commit.reads:
            if read.artifact_id == artifact_id and read.version < current.version:
                dependents.append(artifact)
                break
    return dependents


def stale_artifacts(context: Context) -> list[Artifact[Any]]:
    """Artifacts whose producing-commit reads are now older than current state."""
    return [
        context._artifacts[aid] for aid in context._stale if aid in context._artifacts
    ]


def has_stale(context: Context) -> bool:
    return bool(context._stale)


def recompute_stale(context: Context) -> None:
    """Full rebuild of `_stale` after a bulk rewrite."""
    stale: set[str] = set()
    for artifact_id in context._artifacts:
        commit = producing_commit(context, artifact_id)
        if commit is None:
            continue
        for read in commit.reads:
            current = context._artifacts.get(read.artifact_id)
            if current is not None and current.version > read.version:
                stale.add(artifact_id)
                break
    context._stale = stale


def reindex_by_type(context: Context) -> None:
    """Full rebuild of `_by_type` after a bulk rewrite."""
    by_type: dict[type, set[str]] = {}
    for aid, artifact in context._artifacts.items():
        by_type.setdefault(type(artifact.data), set()).add(aid)
    context._by_type = by_type


__all__ = [
    "dependents_of",
    "has_stale",
    "producing_commit",
    "recompute_stale",
    "reindex_by_type",
    "stale_artifacts",
]
