"""python -m reactifact — inspect & visualize the runtime as Mermaid.

Subcommands:

    graph    static agent blueprint from a module path ("pkg.mod:Attr")
    context  live provenance graph from a saved session (KV backend)
    trace    run diagram from a trace store (SQLite)
    replay   deterministic state replay of a saved session
    branch   persistent forks over a KV backend
    scenario run reactifact.testing scenarios (separate from pytest)
    skills   list/install the bundled agent skills
    eval     online-evaluate runs from a trace store

Examples:

    python -m reactifact graph examples.knowledge.agents
    python -m reactifact context examples/knowledge/sessions/traces.sqlite3
    python -m reactifact trace traces.db
    python -m reactifact skills install --target both

Everything prints Mermaid source to stdout — paste it into GitHub, Notion or
mermaid.live.
"""

from __future__ import annotations

import argparse
import sys

from .. import __version__
from . import branch, context, eval, graph, replay, scenario, skills, trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reactifact",
        description="Inspect & visualize reactifact agents, contexts and traces as Mermaid.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    for module in (graph, context, trace, replay, branch, scenario, skills, eval):
        module.add_parser(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Make local modules importable the same way `python -m reactifact` does —
    # the installed console-script entry point doesn't prepend cwd on its own.
    if "" not in sys.path:
        sys.path.insert(0, "")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        print(
            f"reactifact {__version__} — inspect & visualize agents, contexts and"
            " traces as Mermaid.\n"
        )
        print("Render what your agent app already did:")
        print("  reactifact graph   <module:Agent>   static blueprint")
        print("  reactifact trace   <trace.db>       run diagram")
        print("  reactifact context <sessions.sqlite3>  live provenance graph")
        print("  reactifact replay  <sessions.sqlite3>  deterministic replay")
        print("  reactifact branch  <path> <session> <action>  persistent forks")
        print("  reactifact scenario <module>  run reactifact.testing scenarios\n")
        print("  reactifact skills  install   add agent skills to your project\n")
        print("  reactifact eval    <traces.db>  online-evaluate finished runs\n")
        print("Run 'reactifact <command> --help' for details, or try an example:")
        print("  uv run python ./examples/llm_ladder/level1.py")
        parser.print_help()
        return 0
    return int(args.func(args))
