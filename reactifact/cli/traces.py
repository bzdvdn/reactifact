"""`reactifact traces prune` — apply the trace retention policy."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..tracing import RetentionPolicy, TraceStore


def cmd_prune(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"no trace store found at {args.path!r}", file=sys.stderr)
        return 1
    policy = RetentionPolicy(
        max_runs=args.keep,
        max_age_days=args.days,
        keep_failed=args.keep_failed,
        keep_tags=frozenset(args.keep_tag or ()),
    )
    store = TraceStore(str(path), retention=policy)
    try:
        removed = asyncio.run(store.prune(dry_run=args.dry_run))
    finally:
        store.close()
    verb = "would delete" if args.dry_run else "deleted"
    print(f"{verb} {removed} trace(s)")
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_traces = sub.add_parser("traces", help="trace-store maintenance")
    actions = p_traces.add_subparsers(dest="traces_command")
    p_prune = actions.add_parser("prune", help="apply the retention policy")
    p_prune.add_argument("path", help="trace SQLite db")
    p_prune.add_argument("--keep", type=int, default=200, help="newest runs to keep")
    p_prune.add_argument(
        "--days", type=float, default=None, help="also delete runs older than N days"
    )
    p_prune.add_argument(
        "--keep-failed", action="store_true", help="never delete non-completed runs"
    )
    p_prune.add_argument(
        "--keep-tag",
        action="append",
        metavar="TAG",
        help="never delete runs carrying this tag (repeatable)",
    )
    p_prune.add_argument(
        "--dry-run", action="store_true", help="report the count without deleting"
    )
    p_prune.set_defaults(func=cmd_prune)
