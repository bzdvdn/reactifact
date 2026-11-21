"""Code-review agents — parse, review, report."""

from __future__ import annotations

from reactifact import Agent

from .steps import BuildReportStep, ParseDiffStep, ReviewFileStep


class ParseAgent(Agent):
    name = "parse"
    steps = [ParseDiffStep()]


class ReviewAgent(Agent):
    name = "review"
    steps = [ReviewFileStep()]


class ReportAgent(Agent):
    name = "report"
    steps = [BuildReportStep()]


AGENTS = [ParseAgent(), ReviewAgent(), ReportAgent()]

__all__ = ["AGENTS", "ParseAgent", "ReportAgent", "ReviewAgent"]
