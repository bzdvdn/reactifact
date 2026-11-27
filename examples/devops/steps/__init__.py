"""devops steps — routing, domain reports, reply rendering, curated memory.

Mirrors the pipeline: `router` (question → LLM agent's problem artifact) →
`reports` (ToolAnswer of each agent → domain report) → `reply` (report →
chat reply); `memory` records a post-commit memory intent for cross-session
recall. `common` holds the routing helpers/fallback lexicons. Flat re-exports
keep the agent containers' imports stable.
"""

from .memory import remember_report
from .reply import render_reply
from .reports import build_ansible_report, build_gitlab_report, build_k8s_report
from .router import route_problem

__all__ = [
    "build_ansible_report",
    "build_gitlab_report",
    "build_k8s_report",
    "remember_report",
    "render_reply",
    "route_problem",
]
