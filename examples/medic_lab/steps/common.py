"""Shared helpers and constants for the medic-lab pipeline.

Deterministic text tools (lean/negation/token) that the stages, the evaluator
and the steering all reuse. No LLM here (§67).
"""

from __future__ import annotations

import re

from pydantic import BaseModel
from reactifact import Artifact, Context

from ..models import (
    Hypothesis,
)

#: Maximum steering rounds (initial + deepens) before an automatic report.
MAX_DEPTH = 2
#: How many best-relevance refs per hypothesis channel round (§24).
SCOUT_LIMIT = 4

_NEGATIONS = (
    "no",
    "not",
    "negligible",
    "does not",
    "do not",
    "never",
    "without",
    "against",
    "cannot",
    "не",
    "нет",
    "нельзя",
    "без",
)
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "it",
        "its",
        "this",
        "that",
        "how",
        "what",
        "why",
        "which",
        "does",
        "do",
        "you",
        "we",
        "they",
        "he",
        "she",
        "but",
        "so",
        "about",
        "as",
        "has",
        "have",
        "had",
        "will",
        "would",
        "can",
        "your",
    }
)
_SENT_SPLIT_RE = re.compile(r"[.!?]+\s+|\n+")


def scope_key(data: BaseModel) -> str | None:
    """The owning question id: a field, or a PendingQuestion's `notes` entry."""
    value = getattr(data, "question_id", None)
    if isinstance(value, str):
        return value
    notes = getattr(data, "notes", None)
    if isinstance(notes, dict):
        note_value = notes.get("question_id")
        if isinstance(note_value, str):
            return note_value
    return None


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{2,}", text.casefold())) - _STOPWORDS


def token_support(a: str, b: str) -> float:
    """Share of the smaller token set present in the other (0..1)."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def negatory(statement: str) -> bool:
    """True if the statement denies an effect («no/negligible/does not…»)."""
    return any(neg in statement.casefold() for neg in _NEGATIONS)


def split_sentences(text: str) -> list[str]:
    sentences = [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    return sentences or ([text.strip()] if text.strip() else [])


def hypotheses_of(context: Context, question_id: str) -> list[Artifact[Hypothesis]]:
    return [
        h
        for h in context.list_artifacts(Hypothesis)
        if h.data.question_id == question_id
    ]


def all_terminal(context: Context, question_id: str) -> bool:
    hypotheses = hypotheses_of(context, question_id)
    return bool(hypotheses) and all(
        h.data.status in {"supported", "refuted", "inconclusive"} for h in hypotheses
    )
