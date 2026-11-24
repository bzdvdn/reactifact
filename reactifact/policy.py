"""Per-agent execution policy: retry and timeout (§24, §69).

A `Budget` bounds the whole turn; this bounds one agent execution. The
runtime applies it in `Runtime._execute`:

    class Researcher(StepGroup):
        consumes = [Consume(Question)]
        steps = [Search(), Answer()]
        retry = RetryPolicy(max_attempts=3, backoff=0.5)
        timeout = 30.0

Retries re-run the agent from a fresh `Effects` slot, so a failed attempt's
effects are discarded and only the successful patch commits. External side
effects still belong in the outbox (`outbox.act`) — a retry must not send
twice.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """How many times, and after which errors, to retry one agent run.

    `max_attempts` counts the first run too (`1` = no retry). `backoff` is the
    delay after the first failure; it doubles each attempt, capped by
    `max_backoff`. `retry_on` is matched with `isinstance`; keep it narrow
    enough that a programming error is not retried.

    `CancelledError` is never retried (it is not an `Exception`); a timeout
    raised by `StepGroup.timeout` is an `Exception` and is retried only when
    `retry_on` includes `TimeoutError` (the default `(Exception,)` does).

    `max_attempts` bounds the retries; actual token/cost/time spend is still
    tracked by `Budget`, while `RunStats.runs` / `Budget.max_runs` count agent
    executions, not individual attempts.
    """

    max_attempts: int = 3
    backoff: float = 0.5
    max_backoff: float | None = None
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("RetryPolicy.max_attempts must be >= 1")
        if self.backoff < 0:
            raise ValueError("RetryPolicy.backoff must be >= 0")
        if self.max_backoff is not None and self.max_backoff < 0:
            raise ValueError("RetryPolicy.max_backoff must be >= 0")

    def should_retry(self, error: BaseException) -> bool:
        return isinstance(error, self.retry_on)

    def delay(self, attempt: int) -> float:
        """Seconds to wait after failed `attempt` (1-based)."""
        delay = float(self.backoff * (2 ** (attempt - 1)))
        if self.max_backoff is not None:
            delay = min(delay, float(self.max_backoff))
        return delay


__all__ = ["RetryPolicy"]
