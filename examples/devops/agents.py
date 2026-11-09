"""Devops demo: container agents.

Base chat router + three LLM agents (k8s / gitlab / ansible), each with its own
tools and report builder, + render of the answer into the chat. Logic lives in
`steps/`.
"""

from reactifact import Agent, Consume
from reactifact.llm_agent import HITLLMAgent

from .models import (
    AnsibleProblem,
    GitlabProblem,
    K8sProblem,
)
from .prompts import ANSIBLE_SYSTEM, GITLAB_SYSTEM, K8S_SYSTEM
from .steps import (
    AnsibleReportBuilder,
    GitlabReportBuilder,
    K8sReportBuilder,
    RenderReply,
    RouteProblem,
)
from .tools import ansible_run, gitlab_pipeline, gitlab_search, kubectl_get


def _announce_answer(answer: str) -> str:
    return f"Accepted: «{answer}». Checking…"


class RouteAgent(Agent):
    """Base chat: question → problem artifact of the relevant agent (§48)."""

    name = "route"
    steps = [RouteProblem()]


class K8sAgent(HITLLMAgent):
    """k8s diagnostics: its own tool and its own report."""

    name = "k8s"
    system = K8S_SYSTEM
    tools = [kubectl_get]
    consumes = [Consume(K8sProblem)]
    steps = [K8sReportBuilder()]
    max_steps = 4
    max_asks = 1
    resume_announce = staticmethod(_announce_answer)


class GitlabAgent(HITLLMAgent):
    """GitLab problem triage: MR/commit search and CI pipeline status."""

    name = "gitlab"
    system = GITLAB_SYSTEM
    tools = [gitlab_search, gitlab_pipeline]
    consumes = [Consume(GitlabProblem)]
    steps = [GitlabReportBuilder()]
    max_steps = 4
    max_asks = 1
    resume_announce = staticmethod(_announce_answer)


class AnsibleAgent(HITLLMAgent):
    """Ansible problem triage: playbook dry-run."""

    name = "ansible"
    system = ANSIBLE_SYSTEM
    tools = [ansible_run]
    consumes = [Consume(AnsibleProblem)]
    steps = [AnsibleReportBuilder()]
    max_steps = 4
    max_asks = 1
    resume_announce = staticmethod(_announce_answer)


class RenderAgent(Agent):
    """Agent reports → chat replies."""

    name = "render"
    steps = [RenderReply()]
