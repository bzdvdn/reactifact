"""Curated devops memory: record an index intent, never write memory here.

The step is pure: `MemoryStore.remember_after_commit` turns a committed report
into a `PendingAction` with the summary and its provenance. The runtime
dispatches it **after** the generation commits
(`dispatcher=MemoryStore.dispatch`), so a failed turn cannot leave a memory
entry pointing at a report version that never existed.
"""

from __future__ import annotations

from reactifact import Artifact, Effects, PendingAction, Step
from reactifact.provenance import DERIVED_FROM
from reactifact.recipes import MemoryStore

from ..models import AnsibleReport, GitlabReport, K8sReport, UserMsg


class RememberReport(Step):
    """Report → committed `PendingAction` with summary + source session."""

    reads = (K8sReport, GitlabReport, AnsibleReport)
    produces = (PendingAction,)
    provenance = DERIVED_FROM

    async def run(
        self,
        report: Artifact[K8sReport | GitlabReport | AnsibleReport],
        effects: Effects,
    ) -> None:
        text = getattr(report.data, "text", "") or ""
        if not text.strip():
            return None
        source = self.context.get(report.data.query_id)
        source_session = (
            source.data.session_id
            if source is not None and isinstance(source.data, UserMsg)
            else ""
        )
        memory = self.context.resources.require(MemoryStore)
        memory.remember_after_commit(
            effects,
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
        return None


__all__ = ["RememberReport"]
