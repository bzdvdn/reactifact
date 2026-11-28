"""devops reports — ToolAnswer of an agent → its domain report."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Artifact, Context, step
from reactifact.provenance import DERIVED_FROM
from reactifact.tools.use import ToolAnswer

from ..models import AnsibleReport, GitlabReport, K8sReport


def _build(
    context: Context,
    answer: Artifact[ToolAnswer],
    report_type: type[BaseModel],
) -> BaseModel | None:
    """Common report builder: ToolAnswer → domain report with the source id.

    ToolAnswer.query_id points to the problem artifact; its `query_id` holds the
    id of the original message.
    """
    problem = context.get(answer.data.query_id)
    qid = getattr(problem.data, "query_id", "") if problem else ""
    return report_type(query_id=qid, text=answer.data.text)


@step(reads=ToolAnswer, produces=K8sReport, provenance=DERIVED_FROM)
async def build_k8s_report(
    context: Context, answer: Artifact[ToolAnswer]
) -> K8sReport | None:
    report = _build(context, answer, K8sReport)
    return report if isinstance(report, K8sReport) else None


@step(reads=ToolAnswer, produces=GitlabReport, provenance=DERIVED_FROM)
async def build_gitlab_report(
    context: Context, answer: Artifact[ToolAnswer]
) -> GitlabReport | None:
    report = _build(context, answer, GitlabReport)
    return report if isinstance(report, GitlabReport) else None


@step(reads=ToolAnswer, produces=AnsibleReport, provenance=DERIVED_FROM)
async def build_ansible_report(
    context: Context, answer: Artifact[ToolAnswer]
) -> AnsibleReport | None:
    report = _build(context, answer, AnsibleReport)
    return report if isinstance(report, AnsibleReport) else None


__all__ = [
    "build_ansible_report",
    "build_gitlab_report",
    "build_k8s_report",
]
