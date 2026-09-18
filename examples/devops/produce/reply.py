"""devops reply — an agent's domain report becomes the chat reply."""

from __future__ import annotations

from reactifact import Produce, ProduceCall

from ..models import AnsibleReport, ChatReply, GitlabReport, K8sReport


class RenderReply(Produce[ChatReply]):
    """Agent report → chat reply (stable id reply:<query_id>)."""

    artifact_type = ChatReply
    # Only these three report types should ever trigger a reply — moves the
    # old `isinstance(a.data, (K8sReport, GitlabReport, AnsibleReport))`
    # guard to the declaration instead of the produce body. Combined with
    # `reacts_to`, `call.trigger` is then guaranteed non-None, so no guard
    # body is needed.
    reacts_to = (K8sReport, GitlabReport, AnsibleReport)

    async def produce(self, call: ProduceCall) -> None:
        trigger = call.trigger
        assert trigger is not None
        self.effects.create(
            ChatReply(query_id=trigger.data.query_id, text=trigger.data.text),
            id=f"reply:{trigger.data.query_id}",
        )
        return None
