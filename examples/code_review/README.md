# code_review — a review agent over a unified diff

A small, deterministic-first code-review agent: a diff becomes `ChangedFile`
artifacts, each file gets findings from plain-Python rules (and optionally an
LLM), and the final `ReviewReport` links back to the files it reviewed.

```bash
python -m examples.code_review.main --demo
git diff | python -m examples.code_review.main
python -m examples.code_review.main --diff changes.diff --json
```

No API key → offline rules only. With a provider configured (`from_env()` /
`openai_llm(...)`), pass it to `run_review(..., llm=...)` and LLM findings are
merged with the deterministic ones.

## Flow

```text
ReviewRequest
   └─ parse → ChangedFile
                 └─ review → FileReview (deterministic rules + optional LLM)
                               └─ report → ReviewReport (Markdown)
```

- `steps/parse.py` — unified-diff parsing and added-line numbers.
- `steps/review.py` — rules for `eval`/`exec`, `shell=True`, hardcoded secrets,
  bare `except`, `TODO`, debug `print`; optional `StructuredLLM` review.
- `steps/report.py` — aggregates findings and links the report to each
  `FileReview` (`reviewed` relation).
- `prompts.py` — prompt text outside the steps.

The rules are deliberately boring and explainable; replace/extend them with your
team's review policy.
