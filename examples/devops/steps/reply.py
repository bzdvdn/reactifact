"""devops reply — an agent's domain report becomes the chat reply."""

from __future__ import annotations

from reactifact import step
from reactifact.provenance import SUPPORTED_BY

from ..models import AnsibleReport, ChatReply, GitlabReport, K8sReport


@step(
    reads=(K8sReport, GitlabReport, AnsibleReport),
    produces=ChatReply,
    scope="query_id",
    provenance=SUPPORTED_BY,
)
async def render_reply(
    report: K8sReport | GitlabReport | AnsibleReport,
) -> ChatReply:
    """A report artifact → a chat reply."""
    return ChatReply(query_id=report.query_id, text=report.text)


__all__ = ["render_reply"]
