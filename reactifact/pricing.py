"""reactifact.pricing — the injectable cost model for token/cost budgets.

There is deliberately **no built-in price table**: provider prices change, and
vendoring a stale snapshot would silently misreport cost. Supply a `Pricer` on
`RuntimeResources(pricer=...)` and the framework applies it to every LLM
response's usage when a turn's budget tracks cost.

    def openai_prices(model: str, prompt_tokens: int, completion_tokens: int) -> float:
        # dollars per 1M tokens, e.g. gpt-4o-mini: 0.15 in / 0.60 out
        return (prompt_tokens * 0.15 + completion_tokens * 0.60) / 1_000_000

    resources = RuntimeResources(llm=..., pricer=openai_prices)
    runtime = Runtime(ctx, agents=[...], budget=Budget(max_cost=0.50))
"""

from __future__ import annotations

from collections.abc import Callable

#: `(model, prompt_tokens, completion_tokens) -> cost`. The unit is whatever
#: your `Budget.max_cost` uses (dollars, cents, …) — the framework never
#: interprets it.
Pricer = Callable[[str, int, int], float]


def cost_of(
    pricer: Pricer, model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    """Applies `pricer` and coerces the result to `float`."""
    return float(pricer(model, prompt_tokens, completion_tokens))


__all__ = ["Pricer", "cost_of"]
