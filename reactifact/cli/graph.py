"""`reactifact graph` — static agent blueprint from a module path."""

from __future__ import annotations

import argparse

from ..viz import blueprint
from .common import load_agents
from .config import budget_from_config, load_config


def cmd_graph(args: argparse.Namespace) -> int:
    config = load_config()
    spec = args.agents or config.groups
    if spec is None:
        raise SystemExit(
            "reactifact graph: pass a module or set [tool.reactifact] groups"
        )
    agents = load_agents(spec)
    if args.check:
        from ..validate import validate_contracts

        report = validate_contracts(
            groups=agents,
            seeds=args.seed or config.seeds,
            terminal=args.terminal or config.terminal,
            budget=budget_from_config(config),
        )
        print(report.render())
        return 0 if report.ok else 1
    print(blueprint(agents, title=args.title))
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_graph = sub.add_parser("graph", help="static agent blueprint")
    p_graph.add_argument(
        "agents",
        nargs="?",
        default=None,
        help='module path, e.g. "examples.knowledge.step_groups" '
        "(default: [tool.reactifact] groups)",
    )
    p_graph.add_argument("--title", default="reactifact blueprint")
    p_graph.add_argument(
        "--check",
        action="store_true",
        help="validate consumes/produces contracts instead of rendering Mermaid",
    )
    p_graph.add_argument(
        "--seed",
        action="append",
        metavar="TYPE",
        help="artifact type name supplied externally (repeatable)",
    )
    p_graph.add_argument(
        "--terminal",
        action="append",
        metavar="TYPE",
        help="artifact type name allowed to have no consumer (repeatable)",
    )
    p_graph.set_defaults(func=cmd_graph)
