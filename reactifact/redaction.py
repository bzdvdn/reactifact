"""Redaction hook for observability data (§54, §57).

A trace leaves the process — SQLite, Postgres, Langfuse, a dashboard, a log
line — while the working `Context` stays in memory and, when a session is
saved, is persisted as the *resumable conversation*. Redaction is therefore
applied to **trace text only** (`ArtifactRef.data`, `LLMCall.messages` /
`response`, span and LLM `error`), never to the live state or to persisted
sessions: masking the working copy would corrupt a conversation that is
supposed to resume exactly where it left off. Redact what is observed, not
what is executed.

Opt-in, and non-breaking by default:

    from reactifact import RuntimeResources
    from reactifact.redaction import RegexRedactor

    resources = RuntimeResources(llm=..., redactor=RegexRedactor())

Pass any object with a `redact(text: str) -> str` method as `redactor=`; the
protocol is structural, so a project's own PII scrubber drops in without
subclassing anything. `None` (the default) leaves every trace byte-for-byte as
it was before this hook existed.

The built-in `RegexRedactor` is deliberately conservative — email, US SSN,
IBAN, `Bearer` tokens, and `sk-…`-style API keys. It does **not** guess at
phone numbers or card numbers, because a generic `\\d{13,}` pattern would also
mask legitimate financial figures; pass explicit `patterns=` for those.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Redactor(Protocol):
    """Structural interface for a redaction hook: `redact(text) -> text`.

    Implemented by `RegexRedactor`; any object with this method (a custom PII
    service client, a company scrubber) satisfies it.
    """

    def redact(self, text: str) -> str: ...


#: (name, pattern) pairs applied in order. Conservative on purpose — see the
#: module docstring on why cards/phones are not in the default set.
DEFAULT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("us_ssn", r"\b\d{3}-\d{2}-\d{4}\b"),
    ("iban", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    ("bearer", r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
    ("api_key", r"\b(?:sk|pk|api|token|secret)[-_][A-Za-z0-9]{16,}\b"),
)


class RegexRedactor:
    """Pattern-based `Redactor` with conservative, ready-to-use defaults.

    `patterns` is a sequence of `(name, regex)` pairs; `None` uses
    `DEFAULT_PATTERNS`. Everything that matches is replaced with
    `replacement` (default `"[REDACTED:<name>]"`, so a reader can tell *what*
    was removed without seeing it).
    """

    def __init__(
        self,
        patterns: Sequence[tuple[str, str]] | None = None,
        *,
        replacement: str = "[REDACTED:{name}]",
    ):
        self._patterns = [
            (name, re.compile(pattern))
            for name, pattern in (patterns or DEFAULT_PATTERNS)
        ]
        self._replacement = replacement

    def redact(self, text: str) -> str:
        result = text
        for name, pattern in self._patterns:
            result = pattern.sub(self._replacement.format(name=name), result)
        return result


class NoopRedactor:
    """A `Redactor` that changes nothing — explicit "no redaction"."""

    def redact(self, text: str) -> str:
        return text


__all__ = ["DEFAULT_PATTERNS", "NoopRedactor", "Redactor", "RegexRedactor"]
