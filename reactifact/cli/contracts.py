"""`reactifact contracts` — static consumes/produces/tools contracts."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..contracts import describe_contracts
from .common import load_agents


def cmd_contracts(args: argparse.Namespace) -> int:
    groups = load_agents(args.module)
    report = describe_contracts(groups)
    text = report.to_json() if args.format == "json" else report.to_markdown()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_contracts = sub.add_parser(
        "contracts",
        help="static agent contracts (consumes/produces/tools) as Markdown or JSON",
    )
    p_contracts.add_argument(
        "module",
        help='module path, e.g. "examples.knowledge.step_groups" or "pkg.mod:Pipeline"',
    )
    p_contracts.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
        help="output format (default: md)",
    )
    p_contracts.add_argument("--out", metavar="PATH", help="write to a file")
    p_contracts.set_defaults(func=cmd_contracts)
