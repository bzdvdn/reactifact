"""Code-review agent — deterministic rules + optional LLM, as a CLI.

python -m examples.code_review.main --demo
git diff | python -m examples.code_review.main
python -m examples.code_review.main --diff changes.diff --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reactifact import Agent, Pipeline, PipelineRunner, RuntimeResources
from reactifact.providers import LLMProvider

from .models import ReviewReport, ReviewRequest
from .steps import BuildReportStep, ParseDiffStep, ReviewFileStep

STEPS = [ParseDiffStep(), ReviewFileStep(), BuildReportStep()]

#: The reusable definition: one agent, typed seed and answer.
PIPELINE = Pipeline(
    agents=[Agent("review", steps=STEPS)],
    seed_type=ReviewRequest,
    answer_type=ReviewReport,
    name="code-review",
)

DEMO_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,4 +1,7 @@
 def run(user_input):
+    print("debug", user_input)
+    eval(user_input)
+    # TODO: validate the input
     return user_input
"""


def run_review(
    diff: str,
    *,
    instructions: str = "",
    llm: LLMProvider | None = None,
) -> ReviewReport | None:
    """Run the review pipeline; returns the final report (or None)."""
    runner = PipelineRunner(PIPELINE, resources=RuntimeResources(llm=llm))
    result = runner.run(ReviewRequest(diff=diff, instructions=instructions))
    report = result.context.latest(ReviewReport)
    return report.data if report is not None else None


def _read_diff(path: str | None) -> str:
    if path is None:
        return sys.stdin.read()
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review a unified diff.")
    parser.add_argument("--diff", help="diff file, or '-' for stdin (default)")
    parser.add_argument("--demo", action="store_true", help="review a built-in diff")
    parser.add_argument("--instructions", default="", help="extra review guidance")
    parser.add_argument("--json", action="store_true", help="print the report model")
    args = parser.parse_args(argv)

    diff = DEMO_DIFF if args.demo else _read_diff(args.diff)
    report = run_review(diff, instructions=args.instructions)
    if report is None:
        print("No review report was produced.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(report.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
