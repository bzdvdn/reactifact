"""Shared helpers and constants for the medic-lab pipeline.

Deterministic text tools (lean/negation/token) that the stages, the evaluator
and the steering all reuse. No LLM here (§67).
"""

from __future__ import annotations

import re

from reactifact import Artifact, Context, Event
from reactifact.interrupt import PendingQuestion

from ..models import (
    Claim,
    Evidence,
    Hypothesis,
    Question,
    ResearchReport,
    SearchDone,
    TypedDoc,
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


def question_id_of(context: Context, event: Event | None) -> str | None:
    """The owning question of an artifact (or the one a PendingQuestion asks about)."""
    artifact = context.get(event.artifact_id) if event is not None else None
    if artifact is None:
        return None
    if isinstance(artifact.data, PendingQuestion):
        return (artifact.data.notes or {}).get("question_id")
    for model in (
        Question,
        Hypothesis,
        Evidence,
        Claim,
        TypedDoc,
        SearchDone,
        ResearchReport,
    ):
        if isinstance(artifact.data, model):
            if isinstance(artifact.data, Question):
                return artifact.id
            return artifact.data.question_id
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
