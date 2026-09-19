"""`reactifact replay` — deterministic state replay of a saved session (§55)."""

from __future__ import annotations

import argparse
import asyncio

from .common import open_store


async def _cmd_replay(args: argparse.Namespace) -> int:
    import sqlite3

    from ..replay import replay_context, replay_summary
    from ..session import SessionStore

    try:
        store = SessionStore(open_store(args.path, args.backend))
        sessions = await store.list_sessions()
    except sqlite3.OperationalError:
        print(f"no session store found at {args.path!r}")
        return 1
    if not sessions:
        print("no sessions found")
        return 1
    session_id = args.session or sessions[-1]
    try:
        context = await replay_context(store, session_id, version=args.version)
    except KeyError as exc:
        print(str(exc))
        return 1
    summary = replay_summary(context)
    print(f"session {session_id!r} — replay v{summary['version']}")
    print(
        f"artifacts: {summary['artifacts']} · relations: {summary['relations']} · "
        f"pending questions: {summary['pending_questions']}"
    )
    for tname, count in sorted(summary["by_type"].items()):
        print(f"  {tname}: {count}")
    if args.verify is not None or args.hash:
        from ..audit import context_hash

        digest = context_hash(context)
        print(f"context sha256: {digest}")
        if args.verify is not None:
            if digest == args.verify:
                print("verified: state matches the expected hash")
            else:
                print(f"MISMATCH: expected {args.verify}")
                return 1
    if args.diagram:
        from ..viz import context_to_mermaid

        print()
        print(context_to_mermaid(context))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    return asyncio.run(_cmd_replay(args))


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_replay = sub.add_parser(
        "replay", help="deterministic state replay of a saved session (§55)"
    )
    p_replay.add_argument(
        "path", help="KV backend file (sessions.sqlite3) or directory"
    )
    p_replay.add_argument(
        "--session", default=None, help="session id (default: latest)"
    )
    p_replay.add_argument(
        "--version", type=int, default=None, help="replay to this commit"
    )
    p_replay.add_argument("--backend", choices=["file", "sqlite"], default="auto")
    p_replay.add_argument(
        "--hash", action="store_true", help="print the context's content hash"
    )
    p_replay.add_argument(
        "--verify",
        default=None,
        metavar="HASH",
        help="exit non-zero unless the context hash equals HASH (reproducibility check)",
    )
    p_replay.add_argument(
        "--diagram", action="store_true", help="also print the provenance graph"
    )
    p_replay.set_defaults(func=cmd_replay)
