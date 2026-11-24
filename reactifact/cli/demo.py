"""`reactifact demo` — a self-contained auditable answer, no API key."""

from __future__ import annotations

import argparse

from ..demo import main as run_demo


def cmd_demo(args: argparse.Namespace) -> int:
    return run_demo(plain=args.plain)


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_demo = sub.add_parser(
        "demo",
        help="run a 3-second auditable answer (no API key, no setup)",
    )
    p_demo.add_argument("--plain", action="store_true", help="disable ANSI colour")
    p_demo.set_defaults(func=cmd_demo)
