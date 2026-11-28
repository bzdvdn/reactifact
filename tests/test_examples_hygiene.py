"""Examples stay on the current framework surface.

The bundled apps are documentation: a removed name or a legacy style that
survives in an example is copied by users. This pins the obvious drift points
without trying to lint every stylistic choice.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEMPLATES = ROOT / "reactifact" / "templates"

#: name → why it should not appear in an example
FORBIDDEN = {
    "removed tool shim": re.compile(
        r"reactifact\.(?:tool_use|native_tool_use|agent_tool)\b"
    ),
    "removed agents API": re.compile(r"\b(?:create_agent|validate_agents)\b"),
    "removed Produce API": re.compile(r"\b(?:Produce|ProduceCall)\b|@produce\b"),
    "legacy grouping API": re.compile(
        r"\b(?:StepGroup|create_step_group)\b|\bgroups\s*="
    ),
    "legacy Agent.produces": re.compile(
        r"\b(?:StepGroup|Agent)\.produces\b|produces\s*=\s*\["
    ),
    "legacy Step kwargs": re.compile(r"\b(?:also_creates|reacts_to)\s*="),
    "legacy effects helpers": re.compile(r"\beffects\.(?:ask|resume|act)\s*\("),
    "direct state mutation": re.compile(r"\bcontext\.state\b|\bartifact\.update\s*\("),
    "legacy StepInvocation": re.compile(r"\bStepInvocation\b"),
}


def test_examples_declare_agent_consumes() -> None:
    """An agent owns its wake contract; examples must spell it out.

    `Agent(steps=...)` can derive triggers from step reads, but that hides the
    actual scheduling contract. Every example agent states `consumes=` (or the
    imperative `triggers=` escape hatch) explicitly.
    """
    problems: list[str] = []
    for path in sorted([*EXAMPLES.rglob("*.py"), *TEMPLATES.rglob("*.py")]):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if name not in {"Agent", "HITLLMAgent", "LLMAgent"}:
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            if "consumes" not in keywords and "triggers" not in keywords:
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
    assert not problems, "agent without explicit consumes/triggers:\n" + "\n".join(
        problems
    )


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
