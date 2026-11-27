"""Review one changed file: deterministic rules + optional LLM reasoning."""

from __future__ import annotations

import re

from reactifact import Artifact, Consume, Ensure, LLMProvider, Scope, Step
from reactifact.structured import structured_llm

from ..models import ChangedFile, FileReview, Finding, ReviewBody, ReviewRequest
from ..prompts import REVIEW_SYSTEM
from .parse import added_lines

_RULES: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (
        re.compile(r"\b(?:eval|exec)\s*\("),
        "error",
        "dynamic-code",
        "Avoid eval/exec on untrusted input.",
    ),
    (
        re.compile(r"\bshell\s*=\s*True\b"),
        "error",
        "shell-true",
        "Avoid shell=True; pass an argument list.",
    ),
    (
        re.compile(r"(?i)\b(?:password|secret|token)\s*=\s*['\"][^'\"]+"),
        "error",
        "hardcoded-secret",
        "Do not hardcode credentials; read them from configuration/secrets.",
    ),
    (
        re.compile(r"\bexcept\s*:"),
        "warning",
        "bare-except",
        "Catch a specific exception instead of a bare except.",
    ),
    (
        re.compile(r"\b(?:TODO|FIXME)\b"),
        "info",
        "todo",
        "Resolve the TODO/FIXME or link it to a tracked issue.",
    ),
    (
        re.compile(r"\bprint\s*\("),
        "info",
        "debug-print",
        "Prefer structured logging over print in library code.",
    ),
)


def _deterministic(path: str, patch: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, text in added_lines(patch):
        for pattern, severity, rule, message in _RULES:
            if pattern.search(text):
                findings.append(
                    Finding(
                        file=path,
                        line=number,
                        severity=severity,  # type: ignore[arg-type]
                        rule=rule,
                        message=message,
                    )
                )
    return findings


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[int | None, str, str]] = set()
    result: list[Finding] = []
    for finding in findings:
        key = (finding.line, finding.rule, finding.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


class ReviewFileStep(Step):
    consumes = (ChangedFile, Consume(ReviewRequest, wakes=False))
    produces = (FileReview,)
    scope = Scope("request_id", owner=ReviewRequest)

    async def run(
        self,
        file: Artifact[ChangedFile],
        request: Artifact[ReviewRequest],
        llm: LLMProvider | None,
    ) -> Ensure:
        findings = _deterministic(file.data.path, file.data.patch)

        if llm is not None:
            instructions = request.data.instructions
            body = await structured_llm(
                llm,
                schema=ReviewBody,
                system=REVIEW_SYSTEM,
                user=(
                    f"File: {file.data.path}\n\n{file.data.patch}\n\n"
                    f"Instructions: {instructions or '(none)'}"
                ),
            )
            if body is not None:
                findings.extend(
                    finding.model_copy(
                        update={
                            "file": file.data.path,
                            "rule": finding.rule or "llm",
                        }
                    )
                    for finding in body.findings
                )

        return Ensure(
            FileReview(
                request_id=file.data.request_id,
                path=file.data.path,
                findings=_dedupe(findings),
            ),
            source=file,
            prefix="review",
        )


__all__ = ["ReviewFileStep"]
