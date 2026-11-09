"""devops reply — an agent's domain report becomes the chat reply."""

from __future__ import annotations

from reactifact import Step

from ..models import AnsibleReport, ChatReply, GitlabReport, K8sReport


class RenderReply(Step):
    """Agent report → chat reply."""

    # Only these three report types should ever trigger a reply — the
    # declaration replaces the old isinstance guard in the body.
    consumes = (K8sReport, GitlabReport, AnsibleReport)
    produces = (ChatReply,)
    scope = "query_id"

    async def run(self, report: K8sReport | GitlabReport | AnsibleReport) -> ChatReply:
        return ChatReply(query_id=report.query_id, text=report.text)


__all__ = ["RenderReply"]
