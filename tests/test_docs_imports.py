"""README and current docs import only real, public reactifact names.

Migration pages are excluded on purpose: they show removed APIs (``Produce``,
``Agent``) next to their replacements.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import reactifact

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
FILES = [
    path for path in FILES if path.is_file() and not path.name.startswith("migration-")
]
_IMPORT = re.compile(
    r"^[ \t]*from (reactifact(?:\.[A-Za-z_]\w*)*) import (\([^)]*\)|[^\n]+)",
    re.M,
)
_IMPORT_MODULE = re.compile(
    r"^[ \t]*import (reactifact(?:\.[A-Za-z_]\w*)*)",
    re.M,
)


def _imported_names(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("("):
        raw = raw[1:-1]
    return [
        name.strip().split(" as ")[0].strip() for name in raw.split(",") if name.strip()
    ]


def test_readme_and_docs_imports_are_public() -> None:
    problems: list[str] = []
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        for module_name, raw in _IMPORT.findall(text):
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:  # noqa: BLE001 — report the offending file
                problems.append(f"{path}: cannot import {module_name}: {exc}")
                continue
            for name in _imported_names(raw):
                if name == "*":
                    continue
                if not hasattr(module, name):
                    problems.append(f"{path}: {module_name}.{name} does not exist")
                elif module_name == "reactifact" and name not in reactifact.__all__:
                    problems.append(
                        f"{path}: {module_name}.{name} is not top-level public"
                    )
                elif getattr(module, "__all__", None) and name not in module.__all__:
                    problems.append(f"{path}: {module_name}.{name} is not public")
        for module_name in _IMPORT_MODULE.findall(text):
            try:
                importlib.import_module(module_name)
            except Exception as exc:  # noqa: BLE001 — report the offending file
                problems.append(f"{path}: cannot import {module_name}: {exc}")
    assert not problems, "\n".join(problems)
