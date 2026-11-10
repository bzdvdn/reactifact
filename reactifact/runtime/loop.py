"""The turn fixpoint loop (generation → commit → next generation)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..budget import RunOutcome

if TYPE_CHECKING:
    from .runtime import Runtime


async def run_generations(runtime: Runtime, limit: int) -> int:
    """Run generations until quiescence, stop, budget exhaustion or `limit`."""
    total_runs = 0
    for _ in range(limit):
        if runtime._stop_requested.is_set():
            runtime.outcome = RunOutcome.STOPPED
            break
        if runtime._budget_exhausted():
            break
        runs = await runtime._arun_once_impl()
        total_runs += runs
        # Continue while the context is not quiescent: a generation that ran
        # no agent can still have left outbox work (or events emitted by the
        # drain) that the next generation must drain (§42).
        if runs == 0 and not runtime._has_pending_work():
            break
    else:
        if runtime.outcome == RunOutcome.COMPLETED:
            runtime.outcome = RunOutcome.ITERATIONS_EXHAUSTED
    return total_runs


__all__ = ["run_generations"]
