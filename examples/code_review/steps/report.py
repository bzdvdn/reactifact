"""Aggregate file reviews into the final Markdown report."""

from __future__ import annotations

from collections import Counter

from reactifact import Artifact, Emit, Step

from ..models import FileReview, ReviewReport


def render_report(request_id: str, reviews: list[FileReview]) -> ReviewReport:
    findings = [finding for review in reviews for finding in review.findings]
    findings.sort(key=lambda f: (f.file, f.line or 0, f.severity, f.message))
    counts = Counter(finding.severity for finding in findings)
    lines = [
        f"# Code review {request_id}",
        "",
        f"{len(reviews)} file(s), {len(findings)} finding(s): "
        f"{counts.get('error', 0)} error, {counts.get('warning', 0)} warning, "
        f"{counts.get('info', 0)} info.",
        "",
    ]
    for finding in findings:
        location = f"{finding.file}:{finding.line}" if finding.line else finding.file
        lines.append(
            f"- **{finding.severity}** `{location}` "
            f"({finding.rule or 'general'}): {finding.message}"
        )
    if not findings:
        lines.append("No issues found in the added lines.")
    return ReviewReport(
        request_id=request_id,
        file_count=len(reviews),
        finding_count=len(findings),
        text="\n".join(lines),
    )


class BuildReportStep(Step):
    consumes = (FileReview,)
    produces = (ReviewReport,)

    async def run(self, review: Artifact[FileReview]) -> Emit:
        request_id = review.data.request_id
        artifacts = [
            artifact
            for artifact in self.context.list_artifacts(FileReview)
            if artifact.data.request_id == request_id
        ]
        return Emit(
            render_report(request_id, [artifact.data for artifact in artifacts]),
            id=f"review-report:{request_id}",
            links=tuple(("reviewed", artifact) for artifact in artifacts),
        )


__all__ = ["BuildReportStep", "render_report"]
