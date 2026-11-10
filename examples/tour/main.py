"""tour — a compact feature tour of reactifact.

Four independent scenes, all runnable offline:

- `state`    — typed artifacts, version history, diff, compaction;
- `reactive` — typed inputs, `Latest`, `Wakes`, debounced fan-out;
- `durable`  — SQLite session, process restart, pending-event resume, replay;
- `model`    — the only scene that can call an LLM (`--llm`), with an honest
               deterministic fallback when no provider is configured.

Run:  .venv/bin/python -m examples.tour.main
      .venv/bin/python -m examples.tour.main --scene state
      .venv/bin/python -m examples.tour.main --llm --scene model
      .venv/bin/python -m examples.tour.main --scene durable --db /tmp/tour.sqlite3
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from reactifact.providers import from_env

from .scenes import run_durable, run_model, run_reactive, run_state

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

SCENES = ("all", "state", "reactive", "durable", "model")


def _print_state(report: dict[str, Any]) -> None:
    print("state — versioned artifacts and history")
    print(
        f"  current draft:        {report['current']!r} (revision {report['revision']})"
    )
    print(f"  artifact versions:    {report['versions']}")
    print(f"  at commit 1:          revision {report['historical_revision']}")
    print(f"  diff changed:         {report['changed']}")
    print(f"  compacted commits:    {report['compaction_dropped']}")
    print(f"  versions after:       {report['versions_after_compaction']}")


def _print_reactive(report: dict[str, Any]) -> None:
    print("reactive — typed inputs and debounced fan-out")
    print(f"  topic:                {report['topic']}")
    print(f"  digest artifacts:     {report['digest_count']}")
    print(f"  facts (one run):      {report['facts']}")
    print(f"  max_facts via Latest: {report['max_facts']}")


def _print_durable(report: dict[str, Any]) -> None:
    print("durable — sqlite session, restart, resume, replay")
    print(f"  loaded session:       {report['loaded']}")
    print(f"  resumed result:       {report['done']}")
    print(f"  replayed at v1:       {report['replayed_done']}")
    print(f"  final version:        {report['version']}")


def _print_model(report: dict[str, Any]) -> None:
    print(f"model — LLM step ({report['mode']})")
    print(f"  brief:                {report['brief']}")


async def _run(args: argparse.Namespace) -> int:
    provider = from_env() if args.llm else None
    if args.llm and provider is None:
        print("No LLM provider configured; the model scene uses the fallback.")
        print("Set OPENROUTER_API_KEY or OPENAI_BASE_URL in examples/tour/.env.")

    try:
        if args.scene in ("all", "state"):
            _print_state(await run_state())
        if args.scene in ("all", "reactive"):
            _print_reactive(await run_reactive())
        if args.scene in ("all", "durable"):
            _print_durable(await run_durable(args.db))
        if args.scene in ("all", "model"):
            _print_model(await run_model(provider))
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.tour.main")
    parser.add_argument("--scene", choices=SCENES, default="all")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="use a provider from examples/tour/.env for the model scene",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="keep the durable scene's SQLite session at this path",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
