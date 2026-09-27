from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from .cache import is_cache_hit
from .pricing import Pricer, cost_of
from .providers import LLMProvider, LLMRequest, LLMResponse, LLMResponseChunk


class RunOutcome(StrEnum):
    """Deterministic run outcome (instead of silent nothingness, §58).

    The application routes on it: completed → answer is ready;
    budget_* / iterations_exhausted → not enough resources, show an honest status.
    """

    COMPLETED = "completed"
    ITERATIONS_EXHAUSTED = "iterations_exhausted"
    BUDGET_RUNS_EXCEEDED = "budget_runs_exceeded"
    BUDGET_TIME_EXCEEDED = "budget_time_exceeded"
    BUDGET_TOKENS_EXCEEDED = "budget_tokens_exceeded"
    BUDGET_COST_EXCEEDED = "budget_cost_exceeded"


class Budget(BaseModel):
    """Resource limit for a single run (turn), applied by the runtime."""

    max_runs: int | None = None  # max agent runs
    max_iterations: int | None = None  # max loop generations
    max_seconds: float | None = None  # time budget
    max_tool_calls: int | None = None  # max tool calls executed (LLM agent)
    #: Total tokens (prompt + completion) over the turn. A call is allowed to
    #: finish; the budget stops the *next* generation/loop step, so a run can
    #: overshoot by at most one call — same semantics as `max_seconds`.
    max_tokens: int | None = None
    #: Total cost over the turn, computed from `RuntimeResources.pricer`; with
    #: no pricer configured the limit is inert (a warning is logged at begin).
    max_cost: float | None = None

    @property
    def tracks_llm_usage(self) -> bool:
        """Whether this budget needs an LLM-usage tracker (tokens/cost limits)."""
        return self.max_tokens is not None or self.max_cost is not None


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
    #: LLM usage totalled over the turn. Available without a tracer (the
    #: runtime counts usage itself when the budget tracks it).
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class BudgetTracker:
    """Per-turn LLM usage counters, checked against a `Budget`.

    The runtime creates one per turn when the budget has token/cost limits and
    records every LLM response's usage into it — independent of tracing, which
    is optional (`RecordingLLM` is only installed when a tracer is set).
    """

    __slots__ = ("prompt_tokens", "completion_tokens", "cost")

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record(
        self, *, prompt_tokens: int, completion_tokens: int, cost: float = 0.0
    ) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cost += cost

    def exhausted(self, budget: Budget | None) -> RunOutcome | None:
        """The budget-exceeded outcome, or `None` while still within budget."""
        if budget is None:
            return None
        if budget.max_tokens is not None and self.total_tokens >= budget.max_tokens:
            return RunOutcome.BUDGET_TOKENS_EXCEEDED
        if budget.max_cost is not None and self.cost >= budget.max_cost:
            return RunOutcome.BUDGET_COST_EXCEEDED
        return None


class BudgetLLM(LLMProvider):
    """Counts LLM usage (tokens, and cost via an injected `Pricer`) into a
    `BudgetTracker`, regardless of whether tracing is on.

    The runtime wraps `resources.llm` with this for the turn when the active
    budget has token/cost limits, then restores the original. It is a
    transparent proxy: every other attribute (`.model`, `.aclose`, …) forwards
    to the wrapped provider.
    """

    def __init__(
        self,
        inner: LLMProvider,
        tracker: BudgetTracker,
        *,
        pricer: Pricer | None = None,
    ):
        self._inner = inner
        self._tracker = tracker
        self._pricer = pricer

    def _account(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        prompt = int(usage.get("prompt_tokens") or usage.get("prompt") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("completion") or 0)
        cost = 0.0
        if self._pricer is not None:
            model = str(getattr(self._inner, "model", "") or "")
            cost = cost_of(self._pricer, model, prompt, completion)
        self._tracker.record(
            prompt_tokens=prompt, completion_tokens=completion, cost=cost
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await self._inner.complete(request)
        if not is_cache_hit(response):
            self._account(response.usage)
        return response

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        async for chunk in self._inner.stream(request):
            if chunk.usage:
                self._account(chunk.usage)
            yield chunk

    def __getattr__(self, name: str) -> Any:
        # `model`, `aclose`, provider-specific knobs — forward everything else.
        return getattr(self._inner, name)
