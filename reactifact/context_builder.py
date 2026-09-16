"""reactifact.context_builder — Runtime-level policy for what goes into an
agent's inputs (ranking/truncation), separate from the agent itself.

An `Agent` only declares *which* artifact types/conditions it consumes
(`Consume`); it says nothing about *how many* or *which* matching artifacts
actually go into a produce call when there are more candidates than fit a
prompt. That decision is a `Runtime`-level concern — set it once via
`RuntimeResources(context_builder=...)` and every agent's
`collect_inputs()` (`Agent._collect_inputs`, `agents.py`) routes through it,
so a builder set on the `Context` a `Runtime` was constructed with applies
uniformly, without each `Agent` subclass reimplementing ranking/truncation.

Two implementations ship here: `DefaultContextBuilder` (no-op — today's
"every matching artifact, unranked" behavior) and `TokenBudgetContextBuilder`
(rank candidates, keep a prefix that fits a token budget).

Known limitation (not solved here): `build()` sees the *combined* candidate
list across every `Consume` an agent declares, not each `Consume` separately.
A high-volume type (e.g. many `Evidence`) can starve out a low-volume one
(e.g. the single triggering `Question`) under a tight budget if the
low-volume artifact ranks lower (e.g. it's older). If that bites, pass a
`rank_key` that accounts for it, or budget per `Consume` with several
smaller agents instead of one broad one.

One specific case of that limitation *is* solved: an agent often consumes
one real content type (`Evidence`) plus a marker type it only needs for
triggering (a completion/wake-up artifact with little or no meaningful
text) — see `TokenBudgetContextBuilder`'s `exempt_types`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from .artifacts import Artifact

if TYPE_CHECKING:
    from .agents import Agent
    from .context import Context


class TokenCounter(Protocol):
    """Estimates how many tokens a piece of text costs.

    Exactness is not the contract — see `HeuristicTokenCounter`. Plug in a
    real tokenizer (tiktoken, a provider's count-tokens endpoint, ...) via
    this protocol when you need precision for a specific provider.
    """

    def count(self, text: str) -> int: ...


class HeuristicTokenCounter:
    """Provider-agnostic token estimate: zero dependencies, deliberately
    conservative (overestimates rather than underestimates).

    ~3.5 chars/token instead of the commonly quoted ~4: a budget built on
    this errs toward trimming a bit more context, not toward overflowing a
    provider's window. Good enough for a soft truncation threshold; not a
    substitute for the real `usage` a provider returns after the call
    (`LLMResponse.usage`, `providers/contracts.py`).
    """

    chars_per_token: float = 3.5

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / self.chars_per_token) + 1)


def _default_render(artifact: Artifact[Any]) -> str:
    return str(artifact.data.model_dump_json())


class ContextBuilder(ABC):
    """Ranks/truncates the artifacts a single agent run would otherwise see.

    Called once per agent run with every candidate matched by that agent's
    `consumes` (combined across all of them). Returning the list unchanged
    reproduces today's behavior.
    """

    @abstractmethod
    def build(
        self,
        context: Context,
        agent: Agent,
        candidates: list[Artifact[Any]],
    ) -> list[Artifact[Any]]: ...


class DefaultContextBuilder(ContextBuilder):
    """No-op: every consumes-matched artifact, in collection order."""

    def build(
        self, context: Context, agent: Agent, candidates: list[Artifact[Any]]
    ) -> list[Artifact[Any]]:
        return candidates


class TokenBudgetContextBuilder(ContextBuilder):
    """Ranks candidates (newest-first by default) and keeps a prefix that
    fits `max_tokens`, estimated via `token_counter`.

    At least one *costed* (non-exempt) artifact is always kept, even if it
    alone exceeds the budget — an agent silently getting zero real content
    is worse than one that gets an oversized item; `max_tokens` is a soft
    cap, not a hard clip.

    `exempt_types`: artifact types that cost nothing and never trigger that
    clip — for a marker/trigger type an agent only consumes to wake up
    (little or no meaningful text), not to reason over. Without this, a
    freshly created marker can rank first (newest) and either eat the whole
    budget itself or — worse — silently consume the "at least one" slot,
    leaving zero real content if the budget is then too tight for the next
    (real) candidate. Exempt artifacts are still ranked and still kept
    (still wake the agent up); they just never count toward `max_tokens` or
    toward what counts as "at least one" content item.
    """

    def __init__(
        self,
        *,
        max_tokens: int | None = None,
        token_counter: TokenCounter | None = None,
        rank_key: Callable[[Artifact[Any]], Any] | None = None,
        render: Callable[[Artifact[Any]], str] | None = None,
        exempt_types: tuple[type, ...] = (),
    ):
        self.max_tokens = max_tokens
        self.token_counter = token_counter or HeuristicTokenCounter()
        self.rank_key = rank_key or (lambda a: a.updated_at)
        self.render = render or _default_render
        self.exempt_types = exempt_types

    def build(
        self, context: Context, agent: Agent, candidates: list[Artifact[Any]]
    ) -> list[Artifact[Any]]:
        ranked = sorted(candidates, key=self.rank_key, reverse=True)
        if self.max_tokens is None:
            return ranked
        kept: list[Artifact[Any]] = []
        used = 0
        kept_content = False
        for artifact in ranked:
            exempt = type(artifact.data) in self.exempt_types
            cost = 0 if exempt else self.token_counter.count(self.render(artifact))
            if kept_content and used + cost > self.max_tokens:
                break
            kept.append(artifact)
            used += cost
            if not exempt:
                kept_content = True
        return kept
