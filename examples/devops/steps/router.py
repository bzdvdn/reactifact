"""devops router — a question becomes the right LLM agent's problem artifact."""

from __future__ import annotations

import logging

from reactifact import Artifact, LLMProvider, Progress, Step
from reactifact.authz import Access
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

    reads = (UserMsg,)
    produces = (K8sProblem, GitlabProblem, AnsibleProblem, ChatReply)

    async def run(
        self,
        msg: Artifact[UserMsg],
        access: Access,
        memory: MemoryStore | None,
        llm: LLMProvider | None,
        progress: Progress,
    ) -> K8sProblem | GitlabProblem | AnsibleProblem | ChatReply | None:
        progress("Parsing the question…")

        memory_text = ""
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

        target = await route_target(llm, msg.data.text, memory=memory_text)
        if target != "none" and not access.can("run", target):
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
