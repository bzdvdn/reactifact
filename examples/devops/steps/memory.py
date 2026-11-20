"""Curated devops memory: index report summaries, not every chat message."""

from __future__ import annotations

from reactifact import Artifact, Step
from reactifact.recipes import MemoryStore

from ..models import AnsibleReport, GitlabReport, K8sReport, MemoryNote, UserMsg


class RememberReport(Step):
    """Stores a short report summary with source session/artifact/version.

    The report artifact remains the source of truth; MemoryStore holds only the
    pointer + summary used for cross-session recall. The source session comes
    from the `UserMsg` the report answers (it is stamped by the chat layer), so
    provenance is explicit data, not ambient runtime state.
    """

    consumes = (K8sReport, GitlabReport, AnsibleReport)
    produces = (MemoryNote,)

    async def run(
        self, report: Artifact[K8sReport | GitlabReport | AnsibleReport]
    ) -> MemoryNote:
        memory = self.context.resources.get(MemoryStore)
        text = getattr(report.data, "text", "") or ""
        if memory is not None and text.strip():
            source = self.context.get(report.data.query_id)
            source_session = (
                source.data.session_id
                if source is not None and isinstance(source.data, UserMsg)
                else ""
            )
            await memory.remember_from(
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
        return MemoryNote(text=text)


__all__ = ["RememberReport"]
