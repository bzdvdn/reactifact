"""Guardrails — policy checks on artifacts at the commit boundary (§57).

A guardrail validates (or rewrites) an artifact's data *before the runtime
commits it*: unlike a middleware wrapped around model messages, it acts on the
typed artifact itself, so the policy is deterministic, testable and
provenance-aware. `RuntimeResources(guardrails=GuardrailPolicy(...))` turns it
on; every `Create`/`Update` an agent produces is checked, and the turn is
blocked (`GuardrailViolation`) or the data redacted before anything lands.

    from reactifact.guardrails import GuardrailPolicy, InjectionGuardrail, PIIGuardrail
    from reactifact.redaction import RegexRedactor

    resources = RuntimeResources(
        guardrails=GuardrailPolicy(
            [PIIGuardrail(redactor=RegexRedactor()), InjectionGuardrail()],
            on_violation="block",
        ),
    )

A guardrail returns a `GuardrailDecision`: `allow`, `redact` (with the
replacement data), or `violation` — the latter means "this is bad, the policy
decides", and the policy resolves it to its `on_violation` (`block` raises,
`flag` records and lets it through, `redact` falls back to a copy with
non-conforming text replaced). A guardrail may also return `block`/`flag`
directly to override the policy for itself.

Guardrails run on **agent-produced** artifacts. To screen a seeded input, call
`policy.evaluate(data, context)` yourself before `context.create(data)`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from .redaction import Redactor

if TYPE_CHECKING:
    from .context import Context

#: `allow` → fine; `violation` → defer to the policy's `on_violation`; `redact`
#: → replace the data; `block`/`flag` → the guardrail's own verdict.
GuardrailAction = Literal["allow", "violation", "redact", "block", "flag"]


@dataclass(frozen=True)
class GuardrailDecision:
    """A guardrail's verdict on one artifact's data."""

    action: GuardrailAction = "allow"
    reason: str = ""
    data: Any = None
    guardrail: str = ""


class GuardrailViolation(Exception):
    """Raised when a guardrail blocks an agent-produced artifact.

    Propagates out of `Runtime.arun()` (unless `isolate_errors=True`, where the
    agent's generation is dropped like any other isolated error).
    """

    def __init__(self, guardrail: str, reason: str, artifact_type: str = "") -> None:
        self.guardrail = guardrail
        self.reason = reason
        self.artifact_type = artifact_type
        target = f" {artifact_type!r}" if artifact_type else ""
        super().__init__(f"guardrail {guardrail!r} blocked{target}: {reason}")


@runtime_checkable
class Guardrail(Protocol):
    """Structural interface: `check(data, context) -> GuardrailDecision`."""

    name: str

    def check(self, data: Any, context: Context) -> GuardrailDecision: ...


#: Conservative prompt-injection tells. Deliberately small and overrideable —
#: a lexicon is a filter, not a proof; pair it with an LLM check for high stakes.
DEFAULT_INJECTION_PATTERNS: tuple[str, ...] = (
    r"(?i)\bignore (all )?(previous|prior|above) (instructions|prompts)\b",
    r"(?i)\bdisregard (all )?(previous|prior|above)\b",
    r"(?i)\byou are now\b",
    r"(?i)\breveal (your |the )?(system|developer) prompt\b",
    r"(?i)\bpretend (to be|you are)\b",
    r"(?i)\bact as (if|though) you\b",
)


def _string_fields(data: Any, fields: Sequence[str] | None) -> list[tuple[str, str]]:
    """The `(field, value)` string pairs of a model (or a bare string)."""
    if isinstance(data, str):
        return [("", data)]
    model_fields = getattr(type(data), "model_fields", None)
    if model_fields is None:
        return []
    names = list(fields) if fields is not None else list(model_fields)
    out: list[tuple[str, str]] = []
    for name in names:
        value = getattr(data, name, None)
        if isinstance(value, str):
            out.append((name, value))
    return out


def _replace_fields(data: Any, updates: dict[str, str]) -> Any:
    if isinstance(data, str):
        return updates.get("", data)
    return data.model_copy(update=updates)


@dataclass
class PIIGuardrail:
    """Redacts PII (via any `Redactor`) in the string fields of an artifact.

    Returns `redact` when a substitution changed something, else `allow`, so it
    composes in front of blocking checks: later guardrails see the clean text.
    """

    redactor: Redactor
    name: str = "pii"
    fields: Sequence[str] | None = None

    def check(self, data: Any, context: Context) -> GuardrailDecision:
        _ = context
        updates: dict[str, str] = {}
        for field_name, value in _string_fields(data, self.fields):
            redacted = self.redactor.redact(value)
            if redacted != value:
                updates[field_name] = redacted
        if not updates:
            return GuardrailDecision(guardrail=self.name)
        return GuardrailDecision(
            action="redact",
            reason=f"redacted {', '.join(sorted(k or '<string>' for k in updates))}",
            data=_replace_fields(data, updates),
            guardrail=self.name,
        )


@dataclass
class PatternGuardrail:
    """Blocks/flags data whose string fields match any of `patterns`.

    The general form behind `InjectionGuardrail` and `DenyListGuardrail`.
    """

    patterns: Sequence[str]
    name: str = "pattern"
    fields: Sequence[str] | None = None
    action: GuardrailAction = "violation"

    def __post_init__(self) -> None:
        if not self.patterns:
            raise ValueError(f"{type(self).__name__} needs at least one pattern")
        self._compiled = [re.compile(pattern) for pattern in self.patterns]

    def check(self, data: Any, context: Context) -> GuardrailDecision:
        _ = context
        for field_name, value in _string_fields(data, self.fields):
            for pattern in self._compiled:
                if pattern.search(value):
                    where = f" in {field_name!r}" if field_name else ""
                    return GuardrailDecision(
                        action=self.action,
                        reason=f"matched {pattern.pattern!r}{where}",
                        guardrail=self.name,
                    )
        return GuardrailDecision(guardrail=self.name)


def InjectionGuardrail(
    *,
    patterns: Sequence[str] = DEFAULT_INJECTION_PATTERNS,
    fields: Sequence[str] | None = None,
    action: GuardrailAction = "block",
    name: str = "injection",
) -> PatternGuardrail:
    """Blocks likely prompt-injection text (a small, overrideable lexicon)."""
    return PatternGuardrail(patterns, name=name, fields=fields, action=action)


def DenyListGuardrail(
    patterns: Sequence[str],
    *,
    fields: Sequence[str] | None = None,
    action: GuardrailAction = "block",
    name: str = "deny_list",
) -> PatternGuardrail:
    """Blocks (or flags) text matching a caller-supplied deny list."""
    return PatternGuardrail(patterns, name=name, fields=fields, action=action)


@dataclass
class SizeGuardrail:
    """Blocks a string field longer than `max_chars` (a cheap input cap)."""

    max_chars: int
    field: str = "text"
    action: GuardrailAction = "violation"
    name: str = "size"

    def check(self, data: Any, context: Context) -> GuardrailDecision:
        _ = context
        value = data if isinstance(data, str) else getattr(data, self.field, None)
        if isinstance(value, str) and len(value) > self.max_chars:
            return GuardrailDecision(
                action=self.action,
                reason=f"{self.field} is {len(value)} chars (max {self.max_chars})",
                guardrail=self.name,
            )
        return GuardrailDecision(guardrail=self.name)


@dataclass
class GuardrailPolicy:
    """Ordered guardrails plus the default handling of a violation.

    `on_violation` resolves any `violation` decision a guardrail defers:
    `block` raises `GuardrailViolation`, `flag` records a metric and lets the
    artifact through, `redact` allows it but strips the offending fields. A
    guardrail that returns `block`/`flag`/`redact` itself overrides the policy.
    """

    guardrails: Sequence[Guardrail] = field(default_factory=tuple)
    on_violation: Literal["block", "flag"] = "block"

    def evaluate(self, data: Any, context: Context) -> GuardrailDecision:
        """Runs every guardrail in order; the first hard verdict wins.

        `redact` decisions are applied and checking continues on the redacted
        data (so a PII scrub can run before an injection check).
        """
        current = data
        for guardrail in self.guardrails:
            decision = guardrail.check(current, context)
            if decision.action == "allow":
                continue
            if decision.action == "redact":
                current = decision.data if decision.data is not None else current
                continue
            action = (
                self.on_violation if decision.action == "violation" else decision.action
            )
            return GuardrailDecision(
                action=action,
                reason=decision.reason,
                data=current,
                guardrail=decision.guardrail or guardrail.name,
            )
        if current is not data:
            return GuardrailDecision(action="redact", reason="redacted", data=current)
        return GuardrailDecision()


__all__ = [
    "DEFAULT_INJECTION_PATTERNS",
    "DenyListGuardrail",
    "Guardrail",
    "GuardrailAction",
    "GuardrailDecision",
    "GuardrailPolicy",
    "GuardrailViolation",
    "InjectionGuardrail",
    "PIIGuardrail",
    "PatternGuardrail",
    "SizeGuardrail",
]
