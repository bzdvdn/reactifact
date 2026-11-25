"""python -m reactifact — inspect & visualize the runtime as Mermaid.

Subcommands:

    demo     run a self-contained auditable answer (no API key)
    new      create a project (FastAPI chat or scheduled daemon)
    doctor   check app settings and common boundaries
    graph    static group blueprint from a module path ("pkg.mod:Attr")
    contracts static consumes/produces/tools contracts (Markdown or JSON)
    validate static contract check of a module's groups
    context  live provenance graph from a saved session (KV backend)
    trace    run diagram from a trace store (SQLite)
    replay   deterministic state replay of a saved session
    branch   persistent forks over a KV backend
    scenario run reactifact.testing scenarios (separate from pytest)
    skills   list/install the bundled agent skills
    eval     online-evaluate runs from a trace store

Examples:

    reactifact new app my-app
    reactifact new daemon my-worker
    reactifact demo
    reactifact doctor
    reactifact graph examples.knowledge.agents
    reactifact context examples/knowledge/sessions/traces.sqlite3
    reactifact trace traces.db
    reactifact skills install --target both
    reactifact contracts examples.knowledge.step_groups
    reactifact validate examples.knowledge.step_groups

Inspection commands print Mermaid source to stdout — paste it into GitHub,
Notion or mermaid.live.
"""

from __future__ import annotations

import argparse
import sys

from .. import __version__
from . import (
    branch,
    context,
    contracts,
    demo,
    doctor,
    eval,
    graph,
    new,
    replay,
    run,
    scenario,
    skills,
    trace,
    traces,
    validate,
)


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

    for module in (
        demo,
        graph,
        contracts,
        context,
        trace,
        replay,
        branch,
        scenario,
        skills,
        traces,
        validate,
        eval,
        new,
        run,
        doctor,
    ):
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
        print("Start here:")
        print("  reactifact demo                30-second auditable answer, no key")
        print('  reactifact run <module:pipeline> "text"   run a Pipeline')
        print("  reactifact new app <dir>       FastAPI chat template")
        print("  reactifact new daemon <dir>    scheduled agent loop")
        print("  reactifact doctor              check an app's settings")
        print("  reactifact skills install      add agent skills to your project\n")
        print("Check a pipeline:")
        print("  reactifact validate <module>   static consumes/produces check")
        print("  reactifact contracts <module>  static contracts (md/json)\n")
        print("Inspect a run:")
        print("  reactifact graph   <module:StepGroup>   static blueprint")
        print("  reactifact trace   <trace.db>       run diagram\n")
        print("Advanced:")
        print("  reactifact context <sessions.sqlite3>  live provenance graph")
        print("  reactifact replay  <sessions.sqlite3>  deterministic replay")
        print("  reactifact branch  <path> <session> <action>  persistent forks")
        print("  reactifact scenario <module>  run reactifact.testing scenarios")
        print("  reactifact eval    <traces.db>  online-evaluate finished runs\n")
        print("Install into an app:")
        print("  uv add reactifact              core + CLI")
        print("  uv add 'reactifact[web]'       + FastAPI/uvicorn")
        print("Run 'reactifact <command> --help' for details.")
        parser.print_help()
        return 0
    return int(args.func(args))
