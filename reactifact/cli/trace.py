"""`reactifact trace` — run diagram from a trace store (SQLite)."""

from __future__ import annotations

import argparse


def cmd_trace(args: argparse.Namespace) -> int:
    import asyncio
    import json
    import sqlite3
    from pathlib import Path

    from ..tracing import TraceStore
    from ..tracing.diff import diff_traces
    from ..viz import trace_to_mermaid

    store = TraceStore(args.path)

    async def _run() -> int:
        trace_id = args.run_id
        try:
            if trace_id is None:
                result = await store.query(limit=1)
                if not result["items"]:
                    print("no traces found")
                    return 1
                trace_id = result["items"][0]["id"]
            trace = await store.get(trace_id)
        except sqlite3.OperationalError:
            print(f"no trace store found at {args.path!r}")
            return 1
        if trace is None:
            print(f"trace {trace_id!r} not found")
            return 1
        if args.against:
            other = await store.get(args.against)
            if other is None:
                print(f"trace {args.against!r} not found")
                return 1
            diff = diff_traces(trace, other)
            if args.format == "md":
                rendered = diff.to_markdown()
            elif args.format == "json":
                rendered = json.dumps(diff.to_dict(), indent=2, ensure_ascii=False)
            else:
                rendered = diff.to_text()
            if args.out:
                Path(args.out).write_text(
                    rendered + ("\n" if not rendered.endswith("\n") else ""),
                    encoding="utf-8",
                )
                print(f"wrote {args.out}")
            else:
                print(rendered)
            return 0
        if args.format == "json":
            rendered = json.dumps(trace.to_dict(), indent=2, ensure_ascii=False)
        elif args.format == "md":
            rendered = trace.to_markdown()
        elif args.format == "html":
            rendered = trace.to_html()
        elif args.format == "text":
            rendered = trace.to_text()
        else:
            rendered = trace_to_mermaid(trace)
        if args.out:
            Path(args.out).write_text(
                rendered + ("\n" if not rendered.endswith("\n") else ""),
                encoding="utf-8",
            )
            print(f"wrote {args.out}")
        else:
            print(rendered)
        return 0

    return asyncio.run(_run())


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_trace = sub.add_parser("trace", help="run diagram from a trace store")
    p_trace.add_argument("path", help="trace SQLite db")
    p_trace.add_argument(
        "run_id", nargs="?", default=None, help="run id (default: latest)"
    )
    p_trace.add_argument(
        "--format",
        default="mermaid",
        choices=("mermaid", "md", "html", "json", "text"),
        help="rendering: Mermaid, Markdown, self-contained HTML, JSON or text",
    )
    p_trace.add_argument("--out", help="write the report to this file")
    p_trace.add_argument(
        "--against",
        metavar="RUN_ID",
        help="compare with this run instead of rendering (trace diff)",
    )
    p_trace.set_defaults(func=cmd_trace)
