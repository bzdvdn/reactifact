"""`reactifact skills` — list and install the bundled agent skills.

reactifact ships official Agent Skills inside the package
(`reactifact/skills/<name>/SKILL.md`), so the skills a user installs always
match the version of reactifact they have. Coding agents discover skills from a
project's `.agents/skills/` (Codex, Cursor, Gemini CLI, Copilot, …); Claude
Code also reads `.claude/skills/`. This command copies the bundled skills into
one or both of those directories:

    python -m reactifact skills list
    python -m reactifact skills install                 # -> .agents/skills
    python -m reactifact skills install --target both    # + .claude/skills
    python -m reactifact skills show reactifact
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

#: Where agents look for project-local skills, per target name.
_TARGET_DIRS = {
    "agents": Path(".agents") / "skills",
    "claude": Path(".claude") / "skills",
}


def _skills_root() -> Path:
    """The bundled skills directory (inside the installed package)."""
    return Path(__file__).resolve().parent.parent / "skills"


def _skill_dirs() -> list[Path]:
    root = _skills_root()
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if (path / "SKILL.md").is_file())


def _frontmatter(path: Path) -> dict[str, str]:
    """Parses the leading `---` frontmatter block of a SKILL.md."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for line in text[3:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            data[key.strip()] = value.strip()
    return data


def _find(name: str) -> Path | None:
    for path in _skill_dirs():
        if path.name == name:
            return path
    return None


def cmd_list(args: argparse.Namespace) -> int:
    skills = _skill_dirs()
    if not skills:
        print("no bundled skills found")
        return 1
    width = max(len(path.name) for path in skills)
    for path in skills:
        meta = _frontmatter(path / "SKILL.md")
        print(f"{path.name:<{width}}  {meta.get('description', '')}")
    print("\ninstall with: reactifact skills install [name ...]")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    path = _find(args.name)
    if path is None:
        print(f"unknown skill: {args.name!r}")
        return 1
    print((path / "SKILL.md").read_text(encoding="utf-8"), end="")
    return 0


def _targets(args: argparse.Namespace) -> list[Path]:
    if args.to is not None:
        return [Path(args.to)]
    if args.target == "both":
        return list(_TARGET_DIRS.values())
    return [_TARGET_DIRS[args.target]]


def cmd_install(args: argparse.Namespace) -> int:
    skills = _skill_dirs()
    if not skills:
        print("no bundled skills found")
        return 1

    names = args.names or [path.name for path in skills]
    selected: list[Path] = []
    for name in names:
        path = _find(name)
        if path is None:
            available = ", ".join(sorted(p.name for p in skills))
            print(f"unknown skill: {name!r} (available: {available})")
            return 1
        selected.append(path)

    installed = 0
    for target in _targets(args):
        for source in selected:
            destination = target / source.name
            if destination.exists() and not args.force:
                print(f"skip  {destination} (exists; use --force to overwrite)")
                continue
            if destination.exists():
                shutil.rmtree(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
            print(f"install {destination}")
            installed += 1

    if installed == 0:
        print("nothing to install (already present; use --force to overwrite)")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    _ = args
    print("reactifact skills — list or install the bundled agent skills\n")
    print("  reactifact skills list")
    print("  reactifact skills show <name>")
    print("  reactifact skills install [name ...] [--target agents|claude|both]")
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_skills = sub.add_parser("skills", help="list/install the bundled agent skills")
    actions = p_skills.add_subparsers(dest="skills_command")

    p_list = actions.add_parser("list", help="list bundled skills")
    p_list.set_defaults(func=cmd_list)

    p_show = actions.add_parser("show", help="print a skill's SKILL.md")
    p_show.add_argument("name", help="skill name, e.g. reactifact")
    p_show.set_defaults(func=cmd_show)

    p_install = actions.add_parser("install", help="copy skills into a project")
    p_install.add_argument(
        "names",
        nargs="*",
        help="skill names to install (default: all)",
    )
    p_install.add_argument(
        "--target",
        choices=["agents", "claude", "both"],
        default="agents",
        help="which skills dir to install into (default: .agents/skills)",
    )
    p_install.add_argument(
        "--to",
        default=None,
        help="explicit destination directory (overrides --target)",
    )
    p_install.add_argument(
        "--force",
        action="store_true",
        help="overwrite skills that already exist",
    )
    p_install.set_defaults(func=cmd_install)

    p_skills.set_defaults(func=cmd_skills)
