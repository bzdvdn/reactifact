"""devops reports — ToolAnswer of an agent → its domain report."""

from __future__ import annotations

from pydantic import BaseModel
from reactifact import Produce, ProduceCall
from reactifact.tool_use import ToolAnswer

from ..models import AnsibleReport, GitlabReport, K8sReport


class _ReportBuilder(Produce[BaseModel]):
    """Common report builder: ToolAnswer → domain report.

    The agent has already been filtered by the
    `Consume.by_field(ToolAnswer, "agent", …)` consume, so checking the type
    here is enough. ToolAnswer.query_id points to the problem artifact; its
    `query_id` holds the id of the original message.
    """

    report_type: type[BaseModel] = BaseModel

    async def produce(self, call: ProduceCall) -> None:
        a = call.trigger
        if a is None or not isinstance(a.data, ToolAnswer):
            return None
        problem = call.context.get(a.data.query_id)
        qid = getattr(problem.data, "query_id", "") if problem else ""
        self.effects.create(self.report_type(query_id=qid, text=a.data.text))
        return None


class K8sReportBuilder(_ReportBuilder):
    artifact_type = K8sReport
    report_type = K8sReport


class GitlabReportBuilder(_ReportBuilder):
    artifact_type = GitlabReport
    report_type = GitlabReport


class AnsibleReportBuilder(_ReportBuilder):
    artifact_type = AnsibleReport
    report_type = AnsibleReport
