"""`reactifact new` — create a project from a bundled template.

The template lives inside the installed package, so this works offline and from
an installed wheel as well as from a checkout.
"""

from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path
from typing import Any

_TEMPLATES = {"app": "fastapi_chat", "daemon": "daemon", "agent": "agent"}
_IGNORE = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "data",
}


def _copy_tree(source: Any, destination: Path) -> None:
    """Copy a `Traversable` tree without assuming a real filesystem source."""
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if child.name in _IGNORE or child.name.endswith(".pyc"):
            continue
        target = destination / child.name
        if child.is_dir():
            _copy_tree(child, target)
        else:
            target.write_bytes(child.read_bytes())


def cmd_new_template(args: argparse.Namespace) -> int:
    template = files("reactifact.templates").joinpath(_TEMPLATES[args.template_key])
    destination = Path(args.directory).expanduser().resolve()
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        print(f"reactifact new: destination is not empty: {destination}")
        return 1
    if not template.is_dir():
        print(f"reactifact new: bundled template is missing: {template}")
        return 1

    _copy_tree(template, destination)
    print(f"Created {destination}")
    print()
    print("Next:")
    print(f"  cd {destination}")
    if args.template_key == "agent":
        print("  uv sync")
        print('  uv run reactifact run main:pipeline "hello"')
        print("  uv run reactifact trace")
    else:
        print("  uv sync --extra dev")
        print("  cp .env.example .env")
        if args.template_key == "daemon":
            print("  uv run python -m app.main")
        else:
            print("  uv run reactifact dev")
    return 0


def cmd_new_help(args: argparse.Namespace) -> int:
    args.new_parser.print_help()
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_new = sub.add_parser("new", help="create a project from a bundled template")
    p_new.set_defaults(func=cmd_new_help, new_parser=p_new)
    templates = p_new.add_subparsers(dest="template")

    p_app = templates.add_parser(
        "app", help="FastAPI chat: auth, sessions, SSE, traces, settings"
    )
    p_app.add_argument(
        "directory",
        nargs="?",
        default="fastapi-chat",
        help="destination directory (must be empty or absent)",
    )
    p_app.set_defaults(func=cmd_new_template, template_key="app")

    p_daemon = templates.add_parser(
        "daemon", help="scheduled agent loop with graceful shutdown"
    )
    p_daemon.add_argument(
        "directory",
        nargs="?",
        default="agent-daemon",
        help="destination directory (must be empty or absent)",
    )
    p_daemon.set_defaults(func=cmd_new_template, template_key="daemon")

    p_agent = templates.add_parser(
        "agent", help="minimal one-file Pipeline (no web, no API key)"
    )
    p_agent.add_argument(
        "directory",
        nargs="?",
        default="my-agent",
        help="destination directory (must be empty or absent)",
    )
    p_agent.set_defaults(func=cmd_new_template, template_key="agent")


__all__ = ["add_parser"]
