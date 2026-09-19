"""recipes.text — deterministic text scoring without embeddings (§8, §67).

Keyword coverage is the neutral fallback where vectors are optional: the English
`knowledge` chat and the Russian `repair` demo rank documents/catalog rows by
how much of the query is present in the text. Everything here is pure and
LLM-free; `keyword_score` is stop-word- and (optionally) stem-aware.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[\wа-яё]{2,}")

EN_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
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
        "into",
        "off",
        "out",
        "over",
        "under",
        "about",
        "up",
        "down",
        "after",
        "before",
        "during",
        "between",
        "through",
        "against",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "can",
        "could",
        "should",
        "how",
        "what",
        "why",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "he",
        "him",
        "his",
        "she",
        "her",
        "they",
        "them",
        "their",
        "we",
        "us",
        "our",
        "you",
        "your",
        "i",
        "me",
        "my",
        "not",
        "no",
        "yes",
        "but",
        "so",
        "then",
        "than",
        "too",
        "very",
        "just",
        "also",
        "because",
        "as",
        "if",
        "much",
        "many",
        "more",
        "most",
    }
)

#: Russian inflectional suffixes (used when `use_stems=True`).
_RU_SUFFIXES = (
    "аться",
    "иться",
    "ами",
    "ями",
    "ой",
    "ий",
    "ый",
    "ое",
    "ее",
    "ить",
    "ать",
    "ять",
    "ом",
    "ем",
    "ам",
    "ям",
    "ия",
    "ию",
    "ией",
    "ых",
    "их",
    "ая",
    "яя",
    "у",
    "ю",
    "о",
    "а",
    "е",
    "ы",
    "и",
    "й",
)


def _fold_plural(term: str) -> str:
    """Crude English plural fold: `refunds` → `refund`, `policies` → `policy`.

    Deliberately conservative (`status`/`analysis`/`class` are left alone) — it
    exists only so a plain-English query matches a plural in the text without an
    embedder or a real stemmer.
    """
    if len(term) > 3 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "us", "is")):
        return term[:-1]
    return term


def stem(word: str) -> str:
    """Truncates common inflectional suffixes of a Russian word.

    «аутентификацию» and «аутентификация» must match where embedders are not
    needed. English words pass through unchanged (the suffix list is Cyrillic).
    """
    w = word.lower()
    changed = True
    while changed:
        changed = False
        for suffix in _RU_SUFFIXES:
            if len(w) - len(suffix) >= 3 and w.endswith(suffix):
                w = w[: -len(suffix)]
                changed = True
                break
    return w


def _tokens(text: str) -> list[str]:
    """Lowercased word tokens; digit-only tokens never match a query term."""
    lower = text.lower()
    return [t for t in _WORD.findall(lower) if not t.isdigit()]


def stem_words(text: str) -> frozenset[str]:
    """Frozenset of word stems in the text (Latin and Cyrillic)."""
    return frozenset(stem(t) for t in _tokens(text))


def keyword_score(
    text: str,
    query: str,
    *,
    stopwords: frozenset[str] = EN_STOPWORDS,
    use_stems: bool = False,
    fold_plurals: bool = False,
) -> float:
    """Coverage of the query terms by the text (0..1), without embedders.

    `stopwords` are removed from both sides (English function words by default);
    with `use_stems=True` both sides are stemmed (Russian morphology), so
    «аутентификация» matches «аутентификацию». `fold_plurals=True` additionally
    folds English plurals (`refunds` → `refund`, `policies` → `policy`), so a
    singular query term matches a plural in the text. Returns 0.0 for an empty
    query.
    """
    text_terms = set(_tokens(text)) - set(stopwords)
    query_terms = set(_tokens(query)) - set(stopwords)
    if not query_terms:
        return 0.0
    if use_stems:
        text_terms = {stem(t) for t in text_terms}
        query_terms = {stem(t) for t in query_terms}
    if fold_plurals:
        text_terms = {_fold_plural(t) for t in text_terms}
        query_terms = {_fold_plural(t) for t in query_terms}
    return sum(1 for term in query_terms if term in text_terms) / len(query_terms)


__all__ = ["EN_STOPWORDS", "keyword_score", "stem", "stem_words"]
