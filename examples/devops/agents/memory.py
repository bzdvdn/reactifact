"""Curated devops memory: record an index intent, never write memory here.

The step is pure: `MemoryStore.remember_later` returns an
`Ensure(PendingAction)` with the summary and its provenance. The runtime
dispatches it **after** the generation commits
(`dispatcher=MemoryStore.dispatch`), so a failed turn cannot leave a memory
entry pointing at a report version that never existed.
"""

from __future__ import annotations

from reactifact import Artifact, Consume, Ensure, PendingAction, Scope
from reactifact.agent import agent
from reactifact.provenance import DERIVED_FROM
from reactifact.recipes import MemoryStore

from ..models import AnsibleReport, GitlabReport, K8sReport, UserMsg


@agent(
    name="memory",
    consumes=[Consume(K8sReport), Consume(GitlabReport), Consume(AnsibleReport)],
    produces=PendingAction,
    provenance=DERIVED_FROM,
    priority=10,
    # `report.query_id` is the source UserMsg artifact id; scope makes the
    # typed UserMsg read resolve to exactly that artifact.
    scope=Scope("query_id", owner=UserMsg),
)
async def remember_report(
    report: Artifact[K8sReport | GitlabReport | AnsibleReport],
    user: Artifact[UserMsg] | None,
    memory: MemoryStore,
) -> Ensure | None:
    """Report → committed `PendingAction` with summary + source session."""
    text = getattr(report.data, "text", "") or ""
    if not text.strip():
        return None
    source_session = user.data.session_id if user is not None else ""
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
