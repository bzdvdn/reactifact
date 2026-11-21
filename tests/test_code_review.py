"""Code-review example: deterministic findings, provenance and LLM merge."""

from __future__ import annotations

from examples.code_review.main import DEMO_DIFF, run_review
from examples.code_review.steps import added_lines, split_diff
from reactifact.providers import FakeLLM


def test_split_diff_and_added_lines():
    blocks = split_diff(DEMO_DIFF)
    assert [path for path, _ in blocks] == ["app.py"]
    assert list(added_lines(blocks[0][1])) == [
        (2, '    print("debug", user_input)'),
        (3, "    eval(user_input)"),
        (4, "    # TODO: validate the input"),
    ]


def test_offline_review_reports_deterministic_findings():
    report = run_review(DEMO_DIFF)
    assert report is not None
    assert report.file_count == 1
    assert report.finding_count >= 3
    assert "eval" in report.text
    assert "dynamic-code" in report.text
    assert "TODO" in report.text


def test_llm_findings_are_merged_with_rules():
    llm = FakeLLM(
        '{"findings":[{"line":3,"severity":"warning","rule":"llm",'
        '"message":"Validate user_input before use."}]}'
    )
    report = run_review(DEMO_DIFF, llm=llm)
    assert report is not None
    assert "llm" in report.text
    assert "Validate user_input" in report.text
