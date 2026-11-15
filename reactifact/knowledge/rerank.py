"""Query-time reranking, including an optional LLM-backed factory.

The knowledge layer ships no fixed reranker policy. A domain application often
knows signals the index does not (document kind, freshness, tenant policy), so
`query(..., rerank=...)` lets it reorder or filter candidates.

`llm_reranker` is a small, opt-in factory for the common LLM case. It is built
on the core `LLMProvider` — the chat provider, not the embedder — and its
prompt is fully replaceable, so a project can encode domain relevance rules
without forking the layer.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel

from ..providers import LLMProvider, LLMRequest, Message
from ..structured import parse_structured
from .errors import ConfigError
from .models import SearchHit

logger = logging.getLogger(__name__)

RerankResult = list[SearchHit] | Awaitable[list[SearchHit]]
"""A reranker may be synchronous or asynchronous."""

RerankFn = Callable[[str, Sequence[SearchHit], int], RerankResult]
"""`(query, candidates, top_k) -> hits`, called after retrieval and `min_score`.

`candidates` is over-fetched (see `query`), so a reranker has room to promote
or demote results before the final `top_k` cut. Return any subset in any order;
the query truncates it to `top_k`.
"""

RerankPrompt = Callable[[str, Sequence[SearchHit], int], str]
"""Builds the user message for `llm_reranker`.

Receives the original query, the over-fetched candidates and the final `top_k`,
so a domain prompt can render metadata such as `kind`, `documentId` or
`sourceUrl`, not just chunk text.
"""

RERANK_OUTPUT_CONTRACT = (
    'Return a single JSON object {"order": [1, 2, ...]} listing every '
    "excerpt number exactly once, most relevant first. No prose, no markdown."
)
"""The machine-readable part a custom rerank prompt must preserve."""

DEFAULT_RERANK_SYSTEM = (
    "You rank excerpts by relevance to a query. " + RERANK_OUTPUT_CONTRACT
)
"""Default system prompt for `llm_reranker`."""

_MAX_EXCERPT_CHARS = 2000


class _RerankOrder(BaseModel):
    order: list[int]


async def apply_rerank(
    rerank: RerankFn,
    text: str,
    hits: Sequence[SearchHit],
    top_k: int,
) -> list[SearchHit]:
    """Run a sync or async reranker and truncate its result to `top_k`."""
    outcome = rerank(text, hits, top_k)
    if inspect.isawaitable(outcome):
        ranked = await outcome
    else:
        ranked = outcome
    return list(ranked)[:top_k]


def render_rerank_prompt(
    query: str,
    hits: Sequence[SearchHit],
    top_k: int,
    *,
    max_excerpt_chars: int = _MAX_EXCERPT_CHARS,
) -> str:
    """Default user message: numbered excerpts, each truncated for ranking.

    `top_k` is accepted for parity with `RerankPrompt`; ranking does not need
    it, but a custom prompt may.
    """
    excerpts = "\n\n".join(
        f"[{index}]\n{_truncate(hit.text, max_excerpt_chars)}"
        for index, hit in enumerate(hits, start=1)
    )
    return f"Query: {query}\n\nExcerpts:\n{excerpts}"


def llm_reranker(
    llm: LLMProvider,
    *,
    system_prompt: str | None = None,
    instructions: str = "",
    user_prompt: RerankPrompt | None = None,
    max_excerpt_chars: int = _MAX_EXCERPT_CHARS,
    temperature: float = 0.0,
    on_error: Callable[[Exception], None] | None = None,
) -> RerankFn:
    """Build an LLM-backed reranker from the core chat provider.

    `llm` is a chat `LLMProvider` (usually `resources.llm`), independent of the
    `EmbeddingProvider` used to build the index. `system_prompt` replaces the
    default system prompt entirely; `instructions` appends domain rules to it;
    `user_prompt` replaces the default numbered-excerpt renderer. If you replace
    the system prompt, keep `RERANK_OUTPUT_CONTRACT` in it.

    Fails soft: a provider error, invalid JSON or a non-permutation response
    calls `on_error` (or logs a warning) and returns the original order,
    truncated to `top_k`. The raw `rerank=` hook stays fail-loud; this is an
    explicit convenience for a network call.
    """
    if llm is None:
        raise ConfigError(
            "llm_reranker requires a chat LLMProvider (e.g. resources.llm)"
        )

    system = system_prompt or DEFAULT_RERANK_SYSTEM
    if instructions.strip():
        system = f"{system}\n\nDomain instructions:\n{instructions.strip()}"
    builder = user_prompt or (
        lambda query, hits, top_k: render_rerank_prompt(
            query,
            hits,
            top_k,
            max_excerpt_chars=max_excerpt_chars,
        )
    )

    async def rerank(
        query: str, hits: Sequence[SearchHit], top_k: int
    ) -> list[SearchHit]:
        if not hits:
            return []
        try:
            order = await _rank_order(
                llm,
                query=query,
                hits=hits,
                system=system,
                user=builder(query, hits, top_k),
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001 — network/parse helper contract
            if on_error is not None:
                on_error(exc)
            else:
                logger.warning(
                    "llm rerank failed; keeping original order",
                    exc_info=True,
                )
            return list(hits[:top_k])
        return [hits[index] for index in order][:top_k]

    return rerank


async def _rank_order(
    llm: LLMProvider,
    *,
    query: str,
    hits: Sequence[SearchHit],
    system: str,
    user: str,
    temperature: float,
) -> list[int]:
    response = await llm.complete(
        LLMRequest(
            messages=[Message.system(system), Message.user(user)],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
    )
    parsed = parse_structured(response.text, _RerankOrder)
    if parsed is None:
        raise ValueError(
            f"rerank response is not a JSON order object: {response.text!r}"
        )
    indices = [number - 1 for number in parsed.order]
    if sorted(indices) != list(range(len(hits))):
        raise ValueError(
            f"rerank response is not a permutation of 1..{len(hits)}: {parsed.order!r}"
        )
    return indices


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


__all__ = [
    "DEFAULT_RERANK_SYSTEM",
    "RERANK_OUTPUT_CONTRACT",
    "RerankFn",
    "RerankPrompt",
    "RerankResult",
    "apply_rerank",
    "llm_reranker",
    "render_rerank_prompt",
]
