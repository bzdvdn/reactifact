"""devops router helpers — hint lexicons, decision schema, LLM route (§68→§67)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from reactifact import Context
from reactifact.structured import StructuredLLM

from ..prompts import ROUTE_SYSTEM

# keyword fallback for when the LLM is unavailable or undecided
_K8S_HINTS = (
    "k8s",
    "kubernetes",
    "кластер",
    "под",
    "поды",
    "pod",
    "pods",
    "деплоймент",
    "deployment",
    "crash",
    "replica",
    "cluster",
    "node",
    "container",
)
_GITLAB_HINTS = (
    "gitlab",
    "mr",
    "merge request",
    "ci",
    "pipeline",
    "пайплайн",
    "коммит",
    "commit",
)
_ANSIBLE_HINTS = ("ansible", "playbook", "плейбук", "inventory")

HELP_TEXT = (
    "I help with k8s, GitLab, and Ansible. For example:\n"
    "• «why is a pod in the cluster crashing?»\n"
    "• «what is the status of the GitLab pipeline?»\n"
    "• «what does the playbook say about the deploy?»"
)


class RouteDecision(BaseModel):
    """Router decision: which system the question belongs to."""

    target: Literal["k8s", "gitlab", "ansible", "none"] = "none"


_route_prompt = StructuredLLM(RouteDecision, system=ROUTE_SYSTEM)


def keyword_target(text: str) -> str:
    low = text.lower()
    if any(hint in low for hint in _K8S_HINTS):
        return "k8s"
    if any(hint in low for hint in _GITLAB_HINTS):
        return "gitlab"
    if any(hint in low for hint in _ANSIBLE_HINTS):
        return "ansible"
    return "none"


async def route_target(context: Context, text: str) -> str:
    """LLM routing (reasoning, §68) with a deterministic keyword fallback (§67)."""
    decision = await _route_prompt.call(context, user=text)
    if decision is not None and decision.target in ("k8s", "gitlab", "ansible"):
        return decision.target
    return keyword_target(text)
