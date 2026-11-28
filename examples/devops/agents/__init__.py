"""Devops demo agents: a chat router, three LLM specialists, render and memory.

Each public capability is one agent. The `HITLLMAgent`s keep their internal
tool loop; the router, renderer and memory index are one-function `@agent`s.
"""

from reactifact import Agent, Consume, Pipeline
from reactifact.llm_agent import HITLLMAgent

from ..models import AnsibleProblem, GitlabProblem, K8sProblem
from ..prompts import ANSIBLE_SYSTEM, GITLAB_SYSTEM, K8S_SYSTEM
from ..tools import (
    ansible_run,
    gitlab_pipeline,
    gitlab_search,
    kubectl_get,
    kubectl_restart,
)
from .memory import remember_report
from .reply import render_reply
from .reports import build_ansible_report, build_gitlab_report, build_k8s_report
from .router import route_problem


def _announce_answer(answer: str) -> str:
    return f"Accepted: «{answer}». Checking…"


#: Base chat: question → problem artifact of the relevant agent (§48).
Route = route_problem

#: k8s diagnostics: read-only tools + an approval-gated restart.
K8sAgent = HITLLMAgent(
    name="k8s",
    system=K8S_SYSTEM,
    tools=[kubectl_get, kubectl_restart],
    consumes=[Consume(K8sProblem)],
    steps=[build_k8s_report],
    max_steps=4,
    max_asks=1,
    resume_announce=_announce_answer,
)

#: GitLab problem triage: MR/commit search and CI pipeline status.
GitlabAgent = HITLLMAgent(
    name="gitlab",
    system=GITLAB_SYSTEM,
    tools=[gitlab_search, gitlab_pipeline],
    consumes=[Consume(GitlabProblem)],
    steps=[build_gitlab_report],
    max_steps=4,
    max_asks=1,
    resume_announce=_announce_answer,
)

#: Ansible problem triage: playbook dry-run.
AnsibleAgent = HITLLMAgent(
    name="ansible",
    system=ANSIBLE_SYSTEM,
    tools=[ansible_run],
    consumes=[Consume(AnsibleProblem)],
    steps=[build_ansible_report],
    max_steps=4,
    max_asks=1,
    resume_announce=_announce_answer,
)

#: Report objects → chat replies.
Render = render_reply

#: Indexes report summaries for cross-session recall (priority: last).
Memory = remember_report

#: The full devops composition; transports share this list.
AGENTS = [Route, K8sAgent, GitlabAgent, AnsibleAgent, Render, Memory]

#: The reusable definition: `Chat(pipeline=PIPELINE)` or `PIPELINE.agents`.
PIPELINE = Pipeline(agents=AGENTS, name="devops")

__all__ = [
    "AGENTS",
    "AnsibleAgent",
    "GITLAB_SYSTEM",
    "GitlabAgent",
    "K8sAgent",
    "Memory",
    "PIPELINE",
    "Render",
    "Route",
]
