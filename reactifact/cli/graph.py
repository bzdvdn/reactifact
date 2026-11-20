"""`reactifact graph` — static agent blueprint from a module path."""

from __future__ import annotations

import argparse

from ..viz import blueprint
from .common import load_agents


def cmd_graph(args: argparse.Namespace) -> int:
    agents = load_agents(args.agents)
    if args.check:
        from ..validate import validate_agents

        report = validate_agents(
            agents,
            seeds=args.seed or (),
            terminal=args.terminal or (),
        )
        print(report.render())
        return 0 if report.ok else 1
    print(blueprint(agents, title=args.title))
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_graph = sub.add_parser("graph", help="static agent blueprint")
    p_graph.add_argument("agents", help='module path, e.g. "examples.knowledge.agents"')
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
