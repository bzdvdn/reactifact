"""`reactifact validate` — static contract check for a module's agents."""

from __future__ import annotations

import argparse

from ..validate import validate_contracts
from .common import load_agents
from .config import budget_from_config, load_config


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config()
    spec = args.module or config.agents
    if spec is None:
        raise SystemExit(
            "reactifact validate: pass a module or set [tool.reactifact] agents"
        )
    groups = load_agents(spec)
    budget = budget_from_config(config)
    report = validate_contracts(
        agents=groups,
        seeds=args.seed or config.seeds,
        terminal=args.terminal or config.terminal,
        budget=budget,
    )
    print(report.render())
    return 0 if report.ok else 1


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_validate = sub.add_parser(
        "validate",
        help="check consumes/produces contracts of a module's agents",
    )
    p_validate.add_argument(
        "module",
        nargs="?",
        default=None,
        help='module path, e.g. "examples.knowledge.agents" '
        "(default: [tool.reactifact] agents)",
    )
    p_validate.add_argument(
        "--seed",
        action="append",
        metavar="TYPE",
        help="artifact type supplied externally (repeatable)",
    )
    p_validate.add_argument(
        "--terminal",
        action="append",
        metavar="TYPE",
        help="artifact type allowed to have no consumer (repeatable)",
    )
    p_validate.set_defaults(func=cmd_validate)
