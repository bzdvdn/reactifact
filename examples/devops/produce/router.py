"""devops router — a question becomes the right LLM agent's problem artifact."""

from __future__ import annotations

from reactifact import Produce, ProduceCall

from ..models import (
    AnsibleProblem,
    ChatReply,
    GitlabProblem,
    K8sProblem,
    UserMsg,
)
from .common import HELP_TEXT, route_target


class RouteProblem(Produce[K8sProblem]):
    """Base chat: question → problem artifact of the relevant LLM agent (§48).

    The LLM makes the routing decision (routing is reasoning, §68); if the LLM
    is unavailable or did not answer — the keyword fallback (§67).
    """

    artifact_type = K8sProblem

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        msg = call.trigger
        if msg is None or not isinstance(msg.data, UserMsg):
            return None
        context.announce("Parsing the question…", kind="status")
        target = await route_target(context, msg.data.text)
        if target == "k8s":
            self.effects.create(K8sProblem(text=msg.data.text, query_id=msg.id))
        elif target == "gitlab":
            self.effects.create(GitlabProblem(text=msg.data.text, query_id=msg.id))
        elif target == "ansible":
            self.effects.create(AnsibleProblem(text=msg.data.text, query_id=msg.id))
        else:
            self.effects.create(
                ChatReply(query_id=msg.id, text=HELP_TEXT),
                id=f"reply:{msg.id}",
            )
        return None
