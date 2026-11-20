"""devops router — a question becomes the right LLM agent's problem artifact."""

from __future__ import annotations

import logging

from reactifact import Artifact, Step
from reactifact.recipes import MemoryStore

from ..models import (
    AnsibleProblem,
    ChatReply,
    GitlabProblem,
    K8sProblem,
    UserMsg,
)
from .common import HELP_TEXT, route_target

logger = logging.getLogger(__name__)


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

        memory_text = ""
        memory = context.resources.get(MemoryStore)
        if memory is not None:
            try:
                hits = await memory.search(msg.data.text, k=3, tags={"kind": "report"})
            except Exception:
                # Recall is an enhancement: a broken/unavailable memory backend
                # must not take the whole routing turn down with it.
                logger.warning("devops memory recall failed; routing without it")
            else:
                memory_text = "\n".join(
                    hit.record.text for hit in hits if hit.score > 0
                )

        target = await route_target(context, msg.data.text, memory=memory_text)
        if target != "none" and not context.resources.authorize("run", target):
            return ChatReply(
                query_id=msg.id,
                text=(
                    f"Access denied: this user may not run the {target!r} "
                    "agent. Ask an admin for the required capability."
                ),
                kind="denied",
            )
        if target == "k8s":
            return K8sProblem(text=msg.data.text, query_id=msg.id)
        if target == "gitlab":
            return GitlabProblem(text=msg.data.text, query_id=msg.id)
        if target == "ansible":
            return AnsibleProblem(text=msg.data.text, query_id=msg.id)
        return ChatReply(query_id=msg.id, text=HELP_TEXT)


__all__ = ["RouteProblem"]
