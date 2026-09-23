from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel


class RunOutcome(StrEnum):
    """Deterministic run outcome (instead of silent nothingness, §58).

    The application routes on it: completed → answer is ready;
    budget_* / iterations_exhausted → not enough resources, show an honest status.
    """

    COMPLETED = "completed"
    ITERATIONS_EXHAUSTED = "iterations_exhausted"
    BUDGET_RUNS_EXCEEDED = "budget_runs_exceeded"
    BUDGET_TIME_EXCEEDED = "budget_time_exceeded"


class Budget(BaseModel):
    """Resource limit for a single run (turn), applied by the runtime."""

    max_runs: int | None = None  # max agent runs
    max_iterations: int | None = None  # max loop generations
    max_seconds: float | None = None  # time budget
    max_tool_calls: int | None = None  # max tool calls executed (LLM agent)


@dataclass
class RunStats:
    """Run summary: how much was done and why it stopped."""

    runs: int
    iterations: int
    outcome: RunOutcome
    #: Wall-clock duration of the turn, in **seconds** (`time.monotonic()`).
    #: Distinct from `RunTrace.duration_ms`, which is in milliseconds.
    duration: float
    #: Agent executions that raised and were isolated (`Runtime(isolate_errors=True)`).
    #: Always 0 when isolation is off — an exception propagates instead (§69).
    errors: int = 0
