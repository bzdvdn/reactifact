"""Curated devops memory: record an index intent, never write memory here.

The step is pure: `MemoryStore.remember_later` returns an
`Ensure(PendingAction)` with the summary and its provenance. The runtime
dispatches it **after** the generation commits
(`dispatcher=MemoryStore.dispatch`), so a failed turn cannot leave a memory
entry pointing at a report version that never existed.
"""

from __future__ import annotations

from reactifact import Artifact, Context, Ensure, PendingAction, step
from reactifact.provenance import DERIVED_FROM
from reactifact.recipes import MemoryStore

from ..models import AnsibleReport, GitlabReport, K8sReport, UserMsg


@step(
    reads=(K8sReport, GitlabReport, AnsibleReport),
    produces=PendingAction,
    provenance=DERIVED_FROM,
)
async def remember_report(
    context: Context,
    report: Artifact[K8sReport | GitlabReport | AnsibleReport],
) -> Ensure | None:
    """Report → committed `PendingAction` with summary + source session."""
    text = getattr(report.data, "text", "") or ""
    if not text.strip():
        return None
    source = context.get(report.data.query_id)
    source_session = (
        source.data.session_id
        if source is not None and isinstance(source.data, UserMsg)
        else ""
    )
    memory = context.resources.require(MemoryStore)
    return memory.remember_later(
        report,
        key=f"report:{report.id}",
        source_session=source_session,
        text=text[:800],
        tags={
            "kind": "report",
            "system": type(report.data).__name__.removesuffix("Report").lower(),
        },
        ttl_seconds=90 * 24 * 3600,
    )


__all__ = ["remember_report"]
