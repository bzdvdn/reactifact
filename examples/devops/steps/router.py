"""devops router — a question becomes the right LLM agent's problem artifact."""

from __future__ import annotations

from reactifact import Artifact, Step

from ..models import (
    AnsibleProblem,
    ChatReply,
    GitlabProblem,
    K8sProblem,
    UserMsg,
)
from .common import HELP_TEXT, route_target


class RouteProblem(Step):
    """Base chat: question → problem artifact of the relevant LLM agent (§48).

    The LLM makes the routing decision (routing is reasoning, §68); if the LLM
    is unavailable or did not answer — the keyword fallback (§67).
    """

    consumes = (UserMsg,)
    produces = (K8sProblem, GitlabProblem, AnsibleProblem, ChatReply)

    async def run(
        self, msg: Artifact[UserMsg]
    ) -> K8sProblem | GitlabProblem | AnsibleProblem | ChatReply | None:
        context = self.context
        context.announce("Parsing the question…", kind="status")
        target = await route_target(context, msg.data.text)
        if target == "k8s":
            return K8sProblem(text=msg.data.text, query_id=msg.id)
        if target == "gitlab":
            return GitlabProblem(text=msg.data.text, query_id=msg.id)
        if target == "ansible":
            return AnsibleProblem(text=msg.data.text, query_id=msg.id)
        return ChatReply(query_id=msg.id, text=HELP_TEXT)


__all__ = ["RouteProblem"]
