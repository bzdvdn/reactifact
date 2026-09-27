"""The bundled agent skills: frontmatter, runnable snippets and the CLI.

Every fenced ```python block in a SKILL.md is executed unless it begins with a
``# not-run`` comment, so the examples shipped to coding agents cannot silently
drift from the API.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import reactifact
from reactifact.cli import main

SKILLS_DIR = Path(reactifact.__file__).resolve().parent / "skills"
SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))
_PY_BLOCK = re.compile(r"```python\n(.*?)```", re.S)


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    end = text.index("\n---", 3)
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            data[key.strip()] = value.strip()
    return data


def _blocks(text: str) -> list[str]:
    return [block for block in _PY_BLOCK.findall(text) if "# not-run" not in block]


def test_skills_are_present() -> None:
    names = {path.parent.name for path in SKILL_FILES}
    assert {
        "reactifact",
        "reactifact-agents",
        "reactifact-llm",
        "reactifact-rag",
        "reactifact-testing",
        "reactifact-eval",
        "reactifact-observability",
        "reactifact-from-langchain",
    } <= names


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_skill_frontmatter(path: Path) -> None:
    meta = _frontmatter(path.read_text(encoding="utf-8"))
    assert meta.get("name") == path.parent.name
    assert len(meta.get("description", "")) > 40


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_skill_python_blocks_run(path: Path) -> None:
    blocks = _blocks(path.read_text(encoding="utf-8"))
    assert blocks, f"{path} has no runnable python block"
    for index, block in enumerate(blocks):
        namespace: dict[str, object] = {}
        try:
            exec(compile(block, f"{path}#block{index}", "exec"), namespace)
        except Exception as exc:  # noqa: BLE001 — surface which snippet failed
            pytest.fail(f"{path.name} block {index} raised {type(exc).__name__}: {exc}")


def test_skills_cli_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["skills", "list"]) == 0
    out = capsys.readouterr().out
    assert "reactifact-agents" in out
    assert "install" in out


def test_skills_cli_show(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["skills", "show", "reactifact"]) == 0
    assert "---" in capsys.readouterr().out
    assert main(["skills", "show", "missing"]) == 1


def test_skills_cli_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["skills", "install"]) == 0
    installed = tmp_path / ".agents" / "skills"
    assert (installed / "reactifact" / "SKILL.md").is_file()
    # references travel with their skill
    assert (installed / "reactifact-agents" / "references" / "consume.md").is_file()
    # a second run is a no-op without --force, then overwrites with it
    assert main(["skills", "install"]) == 0
    assert main(["skills", "install", "--force"]) == 0
    assert main(["skills", "install", "not-a-skill"]) == 1


def test_skills_cli_install_claude_and_single(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["skills", "install", "--target", "claude", "reactifact-eval"]) == 0
    assert (tmp_path / ".claude" / "skills" / "reactifact-eval" / "SKILL.md").is_file()
    assert not (tmp_path / ".claude" / "skills" / "reactifact").exists()

    assert main(["skills", "install", "--target", "both"]) == 0
    assert (tmp_path / ".agents" / "skills" / "reactifact" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "skills" / "reactifact" / "SKILL.md").is_file()
