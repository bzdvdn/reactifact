"""Code-review agents: parse -> per-file review -> report."""

from reactifact import Agent, Consume, Pipeline

from ..models import ChangedFile, FileReview, ReviewReport, ReviewRequest
from .parse import added_lines, parse_diff, split_diff
from .report import build_report, render_report
from .review import review_file

STEPS = [parse_diff, review_file, build_report]

#: The reusable definition: one agent, typed seed and answer.
PIPELINE = Pipeline(
    agents=[
        Agent(
            "review",
            consumes=[
                Consume(ReviewRequest),
                Consume(ChangedFile),
                Consume(FileReview),
            ],
            steps=STEPS,
        )
    ],
    seed_type=ReviewRequest,
    answer_type=ReviewReport,
    name="code-review",
)

__all__ = [
    "PIPELINE",
    "added_lines",
    "build_report",
    "parse_diff",
    "render_report",
    "review_file",
    "split_diff",
]
