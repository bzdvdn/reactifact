"""devops reports — ToolAnswer of an agent → its domain report."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Artifact, Step
from reactifact.tool_use import ToolAnswer

from ..models import AnsibleReport, GitlabReport, K8sReport


class _ReportBuilder(Step):
    """Common report builder: ToolAnswer → domain report.

    The agent has already been filtered by the
    `Consume.by_field(ToolAnswer, "agent", …)` consume, so checking the type
    here is enough. ToolAnswer.query_id points to the problem artifact; its
    `query_id` holds the id of the original message.
    """

    report_type: type[BaseModel] = BaseModel

    async def run(self, answer: Artifact[ToolAnswer]) -> BaseModel | None:
        problem = self.context.get(answer.data.query_id)
        qid = getattr(problem.data, "query_id", "") if problem else ""
        return self.report_type(query_id=qid, text=answer.data.text)


class K8sReportBuilder(_ReportBuilder):
    consumes = (ToolAnswer,)
    produces = (K8sReport,)
    report_type = K8sReport


class GitlabReportBuilder(_ReportBuilder):
    consumes = (ToolAnswer,)
    produces = (GitlabReport,)
    report_type = GitlabReport


class AnsibleReportBuilder(_ReportBuilder):
    consumes = (ToolAnswer,)
    produces = (AnsibleReport,)
    report_type = AnsibleReport


__all__ = [
    "AnsibleReportBuilder",
    "GitlabReportBuilder",
    "K8sReportBuilder",
]
