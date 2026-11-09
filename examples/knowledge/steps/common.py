"""Shared helpers and constants for the knowledge pipeline.

Routing regexes, chat-memory projection and the deterministic claim tools
(regex/lexicon) used by the extraction, verification and answer stages. No LLM
here — deterministic logic stays in code (§67).
"""

from __future__ import annotations

import re
from typing import Any

from reactifact import Artifact, Context, Event

from ..models import Answer, ChatReply, UserQuery

# How many of the best-relevance refs to take from all sources (§24).
SCOUT_LIMIT = 5
# How many of the most relevant fragments to show in the deterministic fallback.
FALLBACK_TOPN = 3
# How many of the most recent dialog entries to keep in chat memory.
CONVERSATION_LIMIT = 8

GREETING_RE = re.compile(
    r"^(hello|hi|hey|good (morning|afternoon|evening))\b",
    re.IGNORECASE,
)
RESEARCH_RE = re.compile(
    r"(token|gpu|how\b|how (much|many)|why\b|what\b|cost|price|configure|"
    r"install|change|authenticat|platform|method|rest|api|pay|invoic|"
    r"bill\w*|monitor|alert|enable|set up|deploy|instruct|plan|error|catalog)",
    re.IGNORECASE,
)
GREETING_TEXT = (
    "Hello! Ask about the product or any documentation question — "
    "I'll find the answer in the docs."
)

_STOPWORDS = {
    "the",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "was",
    "were",
    "a",
    "an",
    "this",
    "that",
    "what",
    "how",
    "his",
    "her",
    "its",
    "their",
    "he",
    "she",
    "we",
    "you",
    "they",
    "them",
    "not",
    "no",
    "yes",
    "but",
    "so",
    "which",
    "who",
    "about",
    "by",
    "at",
    "from",
    "be",
    "been",
    "being",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "also",
    "it",
    "as",
}
_NEGATIONS = (
    "no",
    "not",
    "never",
    "without",
    "against",
)
_SENT_SPLIT_RE = re.compile(r"[.!?]+\s+|\n+")
_INTERESTING_COL_RE = re.compile(
    r"(cost|price|spend|amount|usage|gpu)",
    re.IGNORECASE,
)


def conversation_text(context: Context, current_query_id: str) -> str:
    """Chat memory: the previous conversation (without the current turn) via view (§27)."""
    view = context.view(
        (UserQuery, ChatReply, Answer),
        condition=lambda a: (
            not (
                a.id == current_query_id
                or getattr(a.data, "query_id", "") == current_query_id
            )
        ),
    )
    ordered = sorted(view.artifacts, key=lambda a: a.created_at)
    recent = ordered[-CONVERSATION_LIMIT:]
    if not recent:
        return ""
    lines = [
        f"user: {a.data.text}"
        if isinstance(a.data, UserQuery)
        else f"assistant: {a.data.text}"
        for a in recent
    ]
    return "Conversation:\n" + "\n".join(lines)


def truncate_at_word(text: str, limit: int) -> str:
    """Hard cap at `limit` chars, cut back to the last whitespace so the
    deterministic (no-LLM) fallback doesn't end mid-word."""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > 0 else limit].rstrip()


def user_query(context: Context, event: Event | None) -> UserQuery | None:
    artifact = context.get(event.artifact_id) if event is not None else None
    if artifact is not None and isinstance(artifact.data, UserQuery):
        return artifact.data
    return None


# --- claim verification helpers (§35, §36) ----------------------------------


def claim_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{2,}", text.casefold())) - _STOPWORDS


def token_support(claim: str, source: str) -> float:
    """Share of the claim tokens confirmed in the source text (0..1)."""
    claim_terms = claim_tokens(claim)
    if not claim_terms:
        return 0.0
    source_terms = claim_tokens(source)
    return len(claim_terms & source_terms) / len(claim_terms)


def has_negation(text: str) -> bool:
    lowered = text.casefold()
    return any(neg in lowered for neg in _NEGATIONS)


def split_sentences(text: str) -> list[str]:
    sentences = [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    return sentences or ([text.strip()] if text.strip() else [])


def source_doc_of(context: Context, evidence: Artifact[Any]) -> Artifact[Any] | None:
    docs = context.related(evidence.id, relation="extracted_from")
    return docs[0] if docs else None


def interesting_column_re() -> re.Pattern[str]:
    return _INTERESTING_COL_RE
