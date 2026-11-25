"""`reactifact demo` — a self-contained auditable answer, no API key."""

from __future__ import annotations

import argparse

from ..demo import main as run_demo


def cmd_demo(args: argparse.Namespace) -> int:
    if args.web:
        from ..demo_web import main_web

        return main_web(
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
        )
    return run_demo(plain=args.plain)


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_demo = sub.add_parser(
        "demo",
        help="run a 3-second auditable answer (no API key, no setup)",
    )
    p_demo.add_argument("--plain", action="store_true", help="disable ANSI colour")
    p_demo.add_argument(
        "--web",
        action="store_true",
        help="serve the answer, provenance, contracts and traces in a browser",
    )
    p_demo.add_argument("--host", default="127.0.0.1", help="bind host for --web")
    p_demo.add_argument("--port", type=int, default=8000, help="bind port for --web")
    p_demo.add_argument(
        "--no-open",
        action="store_true",
        help="do not open the browser for --web",
    )
    p_demo.set_defaults(func=cmd_demo)
