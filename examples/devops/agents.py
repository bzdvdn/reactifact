"""Devops demo: a chat router, three LLM specialists, render and memory.

Each `Agent` is a thin container over `Step`s declared in `steps/`. The
`HITLLMAgent` agents are configured by keyword instead of subclassing, so the
whole composition reads as data; `PIPELINE` is the definition both transports
(the console and the web app) share.
"""

from reactifact import Agent, Consume, Pipeline
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
    RememberReport,
    RenderReply,
    RouteProblem,
)
from .tools import (
    ansible_run,
    gitlab_pipeline,
    gitlab_search,
    kubectl_get,
    kubectl_restart,
)


def _announce_answer(answer: str) -> str:
    return f"Accepted: «{answer}». Checking…"


#: Base chat: question → problem artifact of the relevant agent (§48).
Route = Agent("route", steps=[RouteProblem()])

#: k8s diagnostics: read-only tools + an approval-gated restart.
K8sAgent = HITLLMAgent(
    name="k8s",
    system=K8S_SYSTEM,
    tools=[kubectl_get, kubectl_restart],
    consumes=[Consume(K8sProblem)],
    steps=[K8sReportBuilder()],
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
    steps=[GitlabReportBuilder()],
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
    steps=[AnsibleReportBuilder()],
    max_steps=4,
    max_asks=1,
    resume_announce=_announce_answer,
)

#: Report objects → chat replies.
Render = Agent("render", steps=[RenderReply()])

#: Indexes report summaries for cross-session recall (priority: last).
Memory = Agent("memory", steps=[RememberReport()], priority=10)

#: The full devops composition; transports share this list.
AGENTS = [Route, K8sAgent, GitlabAgent, AnsibleAgent, Render, Memory]

#: The reusable definition: `Chat(pipeline=PIPELINE)` or `PIPELINE.agents`.
PIPELINE = Pipeline(agents=AGENTS, name="devops")
