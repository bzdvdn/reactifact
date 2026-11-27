"""Examples stay on the current framework surface.

The bundled apps are documentation: a removed name or a legacy style that
survives in an example is copied by users. This pins the obvious drift points
without trying to lint every stylistic choice.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

#: name → why it should not appear in an example
FORBIDDEN = {
    "removed tool shim": re.compile(
        r"reactifact\.(?:tool_use|native_tool_use|agent_tool)\b"
    ),
    "removed agents API": re.compile(
        r"\b(?:create_agent|validate_agents)\b|\bagents\s*="
    ),
    "removed Produce API": re.compile(r"\b(?:Produce|ProduceCall)\b|@produce\b"),
    "legacy StepGroup.produces": re.compile(
        r"\bStepGroup\.produces\b|produces\s*=\s*\["
    ),
    "legacy Step kwargs": re.compile(r"\b(?:also_creates|reacts_to)\s*="),
    "legacy effects helpers": re.compile(r"\beffects\.(?:ask|resume|act)\s*\("),
    "direct state mutation": re.compile(r"\bcontext\.state\b|\bartifact\.update\s*\("),
    "legacy StepCall": re.compile(r"\bStepCall\b"),
}


def test_examples_do_not_teach_removed_or_legacy_api() -> None:
    problems: list[str] = []
    paths = [*EXAMPLES.rglob("*.py"), *EXAMPLES.rglob("*.ipynb")]
    for path in sorted(paths):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN.items():
            if pattern.search(text):
                problems.append(f"{path.relative_to(ROOT)}: {label}")
    assert not problems, "\n".join(problems)
