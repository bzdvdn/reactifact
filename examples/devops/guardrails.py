"""Devops trust & safety — custom guardrails for the ops assistant.

A guardrail is small: any object with `name` and
`check(data, context) -> GuardrailDecision`. It runs on every artifact the
agents *produce*, before the runtime commits it, so the policy is plain Python
over typed data — no prompt, no model, fully testable.

This module has two **custom** guardrails:

- `ProductionChangeGuardrail` — a domain rule: a destructive change aimed at
  production must cite a change ticket (`CHG-…`). It returns a `violation`
  decision, deferring the verdict to the policy's `on_violation` (block/flag).
- `SecretRedactionGuardrail` — a *rewriting* guardrail: it strips
  `password=…`/`token=…` credentials and returns `redact` with the fixed model.

Both sit alongside the built-ins (`PIIGuardrail`, `InjectionGuardrail`) in
`devops_guardrail_policy()`, which `chat.py` / `web.py` wire onto
`RuntimeResources`. See `safety.py` for a runnable, no-LLM demo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from reactifact import Context
from reactifact.guardrails import (
    GuardrailDecision,
    GuardrailPolicy,
    InjectionGuardrail,
    PIIGuardrail,
)
from reactifact.redaction import RegexRedactor

from .models import UserMsg

#: Destructive operations an ops request could ask for.
_DESTRUCTIVE = re.compile(
    r"(?i)\b(delete|drop|wipe|truncate|destroy|terminate|rm\s+-rf)\b"
)
_PROD = re.compile(r"(?i)\b(prod|production)\b")
_TICKET = re.compile(r"\bCHG-\d+\b")
#: `password=…`, `token: …`, `api_key=…` style credentials in free text.
_SECRET = re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+")


@dataclass
class ProductionChangeGuardrail:
    """A custom guardrail: destructive production change needs a `CHG-` ticket.

    Returns `violation` (defer to the policy) rather than a hard `block`, so the
    same guardrail works in a "block" deployment and a "flag and alert" one.
    """

    name: str = "production_change"
    require_ticket: bool = True

    def check(self, data: Any, context: Context) -> GuardrailDecision:
        _ = context
        text = getattr(data, "text", "")
        if not isinstance(text, str):
            return GuardrailDecision(guardrail=self.name)
        if not (_DESTRUCTIVE.search(text) and _PROD.search(text)):
            return GuardrailDecision(guardrail=self.name)
        if self.require_ticket and _TICKET.search(text):
            return GuardrailDecision(guardrail=self.name)
        return GuardrailDecision(
            action="violation",
            reason="destructive change to production requires a CHG-… ticket",
            guardrail=self.name,
        )


@dataclass
class SecretRedactionGuardrail:
    """A custom guardrail that rewrites data: strips credentials from `text`."""

    name: str = "secret_redaction"

    def check(self, data: Any, context: Context) -> GuardrailDecision:
        _ = context
        text = getattr(data, "text", "")
        if not isinstance(text, str):
            return GuardrailDecision(guardrail=self.name)
        redacted = _SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
        if redacted == text:
            return GuardrailDecision(guardrail=self.name)
        return GuardrailDecision(
            action="redact",
            reason="redacted a credential",
            data=data.model_copy(update={"text": redacted}),
            guardrail=self.name,
        )


def devops_guardrail_policy(
    *, on_violation: Literal["block", "flag"] = "block"
) -> GuardrailPolicy:
    """The ops policy: redact PII/secrets, block injection and unapproved prod changes.

    Order matters — redaction runs first, so the injection check sees clean text.
    """
    return GuardrailPolicy(
        [
            PIIGuardrail(redactor=RegexRedactor()),
            SecretRedactionGuardrail(),
            InjectionGuardrail(),
            ProductionChangeGuardrail(),
        ],
        on_violation=on_violation,
    )


def screen(policy: GuardrailPolicy, context: Context, text: str) -> GuardrailDecision:
    """Screens a *seeded* input.

    Guardrails run on agent-produced artifacts; a raw user message is created
    directly, so an app that wants to refuse before routing calls this and acts
    on a non-`allow` decision itself.
    """
    return policy.evaluate(UserMsg(text=text), context)


__all__ = [
    "ProductionChangeGuardrail",
    "SecretRedactionGuardrail",
    "devops_guardrail_policy",
    "screen",
]
