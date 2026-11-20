"""tour — a compact feature tour of reactifact.

Nine independent scenes, all runnable offline:

- `state`      — typed artifacts, version history, diff, compaction;
- `reactive`   — typed inputs, `Latest`, `Wakes`, debounced fan-out;
- `durable`    — SQLite session, process restart, pending-event resume, replay;
- `model`      — the only scene that can call an LLM (`--llm`), with an honest
                 deterministic fallback when no provider is configured;
- `resilience` — agent retry policy and a per-step deadline;
- `hitl`       — schema-backed question, typed `context.resume`;
- `time_travel`— checkpoints, snapshot, session fork;
- `events`     — typed `Runtime.astream_events` modes;
- `conflicts`  — optimistic `expected_version` conflict + merge policy.

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

from .scenes import (
    run_conflicts,
    run_durable,
    run_events,
    run_hitl,
    run_model,
    run_reactive,
    run_resilience,
    run_state,
    run_time_travel,
)

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

SCENES = (
    "all",
    "state",
    "reactive",
    "durable",
    "model",
    "resilience",
    "hitl",
    "time_travel",
    "events",
    "conflicts",
)


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


def _print_resilience(report: dict[str, Any]) -> None:
    print("resilience — retries and per-step deadlines")
    print(f"  attempts:             {report['attempts']}")
    print(f"  answer:               {report['answer']}")
    print(f"  retry wall time:      {report['retry_ms']} ms")
    print(f"  step timed out:       {report['timed_out']}")
    print(f"  artifacts after:      {report['artifacts_after_timeout']}")


def _print_hitl(report: dict[str, Any]) -> None:
    print("hitl — typed question and resume")
    print(f"  schema attached:      {report['schema_present']}")
    print(f"  answered:             {report['answered']}")
    print(f"  approved:             {report['approved']}")
    print(f"  note:                 {report['note']!r}")
    print(f"  final:                {report['final']}")


def _print_time_travel(report: dict[str, Any]) -> None:
    print("time_travel — checkpoints, snapshot, fork")
    print(f"  checkpoint versions:  {report['versions']}")
    print(f"  live answer:          {report['live']}")
    print(f"  answer at checkpoint: {report['snapshot']}")
    print(f"  fork version:         {report['fork_version']}")
    print(f"  fork answer:          {report['fork']}")


def _print_events(report: dict[str, Any]) -> None:
    print("events — typed RunEvent stream")
    print(f"  first/last:           {report['first']} -> {report['last']}")
    print(f"  kinds:                {', '.join(report['kinds'])}")
    print(f"  streamed text:        {report['delta']!r}")
    print(f"  writes / value adds:  {report['writes']} / {report['value_added']}")
    print(f"  tool starts:          {report['tool_starts']}")
    print(f"  custom announce:      {report['custom']!r}")
    print(f"  outcome:              {report['outcome']}")


def _print_conflicts(report: dict[str, Any]) -> None:
    print("conflicts — optimistic concurrency and merge policy")
    print(f"  conflict raised:      {report['conflict_raised']}")
    print(f"  actual version:       {report['conflict_actual']}")
    print(f"  artifact after:       {report['artifact_after']}")
    print(f"  merge conflict:       {report['merge_conflict']}")
    print(f"  merged with policy:   {report['merged']}")


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
        if args.scene in ("all", "resilience"):
            _print_resilience(await run_resilience())
        if args.scene in ("all", "hitl"):
            _print_hitl(await run_hitl())
        if args.scene in ("all", "time_travel"):
            _print_time_travel(await run_time_travel())
        if args.scene in ("all", "events"):
            _print_events(await run_events())
        if args.scene in ("all", "conflicts"):
            _print_conflicts(await run_conflicts())
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
