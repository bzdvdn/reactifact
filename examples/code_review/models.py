"""Code-review artifacts: a diff becomes files, findings and a report."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "error"]


class ReviewRequest(BaseModel):
    diff: str
    instructions: str = ""


class ChangedFile(BaseModel):
    request_id: str
    path: str
    patch: str


class Finding(BaseModel):
    file: str = ""
    line: int | None = None
    severity: Severity = "warning"
    rule: str = ""
    message: str


class ReviewBody(BaseModel):
    """The LLM's structured findings for one file."""

    findings: list[Finding] = Field(default_factory=list)


class FileReview(BaseModel):
    request_id: str
    path: str
    findings: list[Finding] = Field(default_factory=list)


class ReviewReport(BaseModel):
    request_id: str
    file_count: int = 0
    finding_count: int = 0
    text: str = ""
