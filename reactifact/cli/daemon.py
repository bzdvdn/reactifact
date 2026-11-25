"""`reactifact daemon` — run a Pipeline on an interval."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from ..daemon import Daemon
from .common import DEFAULT_TRACE_PATH, load_attr, load_pipeline


def _configure_trace(
    pipeline: Any, args: argparse.Namespace
) -> tuple[str | None, Any | None]:
    if args.no_trace:
        pipeline.tracer = None
        pipeline.trace_store = None
        return None, None
    if pipeline.trace_store is not None:
        return None, None

    from ..tracing import Tracer, TraceStore

    path = args.trace or DEFAULT_TRACE_PATH
    store = TraceStore(path)
    pipeline.trace_store = store
    pipeline.tracer = Tracer(store=store)
    return path, store


def cmd_daemon(args: argparse.Namespace) -> int:
    pipeline = load_pipeline(args.pipeline)
    seed_source = load_attr(args.seed)
    if not callable(seed_source):
        raise SystemExit(f"{args.seed!r} must be callable")

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    trace_path, created_store = _configure_trace(pipeline, args)
    daemon = Daemon(
        pipeline,
        seed_source=seed_source,
        interval=args.interval,
        run_once=args.run_once,
        max_ticks=args.max_ticks,
        raise_on_error=args.run_once,
        install_signal_handlers=not args.run_once,
    )
    try:
        ticks = asyncio.run(daemon.serve())
    except KeyboardInterrupt:
        ticks = daemon.ticks
    finally:
        if created_store is not None:
            created_store.close()

    if trace_path:
        print(f"trace: {trace_path}", file=sys.stderr)
    print(f"daemon: {ticks} tick(s)")
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_daemon = sub.add_parser(
        "daemon", help="run a Pipeline on an interval, with graceful shutdown"
    )
    p_daemon.add_argument("pipeline", help="module:attr resolving to a Pipeline")
    p_daemon.add_argument(
        "--seed",
        required=True,
        metavar="FUNC",
        help="module:func(tick) returning a seed model, or None to skip the tick",
    )
    p_daemon.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="seconds between ticks (default: 60)",
    )
    p_daemon.add_argument(
        "--run-once",
        action="store_true",
        help="run exactly one tick (cron/smoke mode)",
    )
    p_daemon.add_argument(
        "--max-ticks",
        type=int,
        default=None,
        help="stop after N ticks",
    )
    p_daemon.add_argument(
        "--trace",
        nargs="?",
        const=DEFAULT_TRACE_PATH,
        default=DEFAULT_TRACE_PATH,
        metavar="PATH",
        help=f"record traces here (default: {DEFAULT_TRACE_PATH})",
    )
    p_daemon.add_argument(
        "--no-trace",
        action="store_true",
        help="do not record traces",
    )
    p_daemon.add_argument(
        "--log-level",
        default="INFO",
        help="logging level (default: INFO)",
    )
    p_daemon.set_defaults(func=cmd_daemon)


__all__ = ["add_parser"]
