"""`reactifact scenario` — run `reactifact.testing` scenarios.

A separate track from `pytest`: scenarios are `@scenario`-decorated functions
(usually wrapping `ScenarioLab.run()`) living in ordinary modules, imported by
dotted path exactly like `reactifact graph <module:Attr>` resolves agents — no
`test_*.py` naming, no pytest collection, so a plain `pytest` run never needs a
model key or a network connection. Point this at one or more modules and it
imports them, runs whatever they registered, and reports PASS/FAIL/SKIP.

A failure is reported pytest-style: a `FAILURES` section, the failing frame(s)
from *your* code with the source line, the exception, and — when the scenario
ran a `ScenarioLab` — the run state (`ScenarioResult.explain()`, unless
`--no-state`). Framework frames (`reactifact/...`) are filtered out.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from ..testing.exceptions import ScenarioSkip
from ..testing.lab import current_result
from ..testing.record import MODE_ENV_VAR
from ..testing.registry import ScenarioCase, collect

PASS, FAIL, ERROR, SKIP = "PASS", "FAIL", "ERROR", "SKIP"
FAILURES_TITLE = "FAILURES"
_WIDTH = 70
#: reactifact's own package dir — frames under it are noise in a scenario's
#: traceback (the harness, not the user's code). `cli/scenario.py` → `cli/` →
#: the package root.
_PACKAGE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class _Outcome:
    """One scenario's result: its status, a one-line detail, the exception (if
    any), and the run state to show on failure."""

    status: str
    detail: str = ""
    exc: BaseException | None = None
    state: str = ""


@dataclass
class ScenarioOutcome:
    """A scenario plus its result and wall-clock time."""

    case: ScenarioCase
    outcome: _Outcome
    elapsed: float


@dataclass
class ScenarioRun:
    """The result of running a batch of scenarios (used by `scenario` and `check`)."""

    outcomes: list[ScenarioOutcome]
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def ok(self) -> bool:
        return not (self.counts[FAIL] or self.counts[ERROR])

    def summary(self) -> str:
        return (
            f"{self.total} scenario(s): {self.counts[PASS]} passed, "
            f"{self.counts[FAIL]} failed, {self.counts[ERROR]} errored, "
            f"{self.counts[SKIP]} skipped"
        )


async def _run_one(case: ScenarioCase) -> _Outcome:
    """Runs one scenario and classifies it: status is one of PASS/FAIL/ERROR/SKIP."""
    try:
        result = case.func()
        if inspect.isawaitable(result):
            await result
    except ScenarioSkip as exc:
        return _Outcome(SKIP, str(exc), exc)
    except AssertionError as exc:
        return _Outcome(FAIL, str(exc) or type(exc).__name__, exc, _state())
    except Exception as exc:  # noqa: BLE001 — report, don't crash the run
        return _Outcome(ERROR, f"{type(exc).__name__}: {exc}", exc, _state())
    return _Outcome(PASS)


def _state() -> str:
    """The run dump for the failure report, if the scenario ran a `ScenarioLab`."""
    result = current_result()
    return result.explain() if result is not None else ""


def _use_color() -> bool:
    return not os.environ.get("NO_COLOR") and sys.stdout.isatty()


_COLOR = {PASS: "32", FAIL: "31", ERROR: "31", SKIP: "33"}


def _styled_status(status: str) -> str:
    code = _COLOR.get(status)
    if code is None or not _use_color():
        return f"{status:<5}"
    return f"\033[{code}m{status:<5}\033[0m"


def _is_framework_frame(filename: str) -> bool:
    try:
        return Path(filename).resolve().is_relative_to(_PACKAGE_DIR)
    except (OSError, ValueError):  # pragma: no cover - defensive
        return False


def _user_frames(tb: TracebackType | None) -> list[traceback.FrameSummary]:
    frames = traceback.extract_tb(tb)
    user = [frame for frame in frames if not _is_framework_frame(frame.filename)]
    return user or frames  # a failure fully inside the harness still renders


def _format_exception(exc: BaseException) -> str:
    lines: list[str] = []
    for frame in _user_frames(exc.__traceback__):
        lines.append(f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}')
        if frame.line:
            lines.append(f"      {frame.line}")
    lines.append(f"E   {type(exc).__name__}: {exc}")
    return "\n".join(lines)


def _print_failure(case: ScenarioCase, outcome: _Outcome, *, show_state: bool) -> None:
    print()
    print(f" {case.name} ".center(_WIDTH, "_"))
    print()
    if outcome.exc is not None:
        print(_format_exception(outcome.exc))
    if show_state and outcome.state:
        print()
        print("--- scenario state ---")
        print(outcome.state)


def run_cases(cases: list[ScenarioCase]) -> ScenarioRun:
    """Runs scenarios in order and collects their outcomes (shared with `check`)."""
    counts = {PASS: 0, FAIL: 0, ERROR: 0, SKIP: 0}
    outcomes: list[ScenarioOutcome] = []
    for case in cases:
        started = time.monotonic()
        outcome = asyncio.run(_run_one(case))
        elapsed = time.monotonic() - started
        counts[outcome.status] += 1
        outcomes.append(ScenarioOutcome(case, outcome, elapsed))

    return ScenarioRun(outcomes=outcomes, counts=counts)


def render_run(run: ScenarioRun, *, show_state: bool = True) -> None:
    """Prints a pytest-style per-scenario report and the final counts."""
    for row in run.outcomes:
        print(
            f"{_styled_status(row.outcome.status)} {row.case.name} ({row.elapsed:.2f}s)"
        )
        if row.outcome.status == SKIP and row.outcome.detail:
            print(f"      {row.outcome.detail}")

    failures = [row for row in run.outcomes if row.outcome.status in (FAIL, ERROR)]
    if failures:
        print()
        print(f" {FAILURES_TITLE} ".center(_WIDTH, "="))
        for row in failures:
            _print_failure(row.case, row.outcome, show_state=show_state)

    print(f"\n{run.summary()}")


def cmd_scenario(args: argparse.Namespace) -> int:
    if args.mode is not None:
        os.environ[MODE_ENV_VAR] = args.mode

    cases = collect(args.modules)
    if args.filter:
        cases = [c for c in cases if args.filter in c.name]
    if not cases:
        print("no scenarios found (check the module path and -k filter)")
        return 1

    run = run_cases(cases)
    render_run(run, show_state=not args.no_state)
    return 0 if run.ok else 1


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_scenario = sub.add_parser(
        "scenario", help="run reactifact.testing scenarios (separate from pytest)"
    )
    p_scenario.add_argument(
        "modules",
        nargs="+",
        help='dotted module path(s) to import, e.g. "examples.repair.scenarios"',
    )
    p_scenario.add_argument(
        "-k",
        "--filter",
        default=None,
        help="only run scenarios whose name contains this substring",
    )
    p_scenario.add_argument(
        "--mode",
        choices=["live", "record", "replay"],
        default=None,
        help=(
            "sets REACTIFACT_SCENARIO_MODE for scenarios built with "
            "reactifact.testing.mode_from_env() — most scenarios default to "
            "'live' or opt out entirely without it"
        ),
    )
    p_scenario.add_argument(
        "--no-state",
        action="store_true",
        help="omit the scenario-state dump from a failure report",
    )
    p_scenario.set_defaults(func=cmd_scenario)
