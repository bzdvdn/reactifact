"""reactifact.prompts — minimal, strict prompt templating (§68).

`PromptTemplate` renders a `{var}`-style template with *declared* variables:
at construction time the placeholders are parsed, at render time a missing
variable is a `KeyError` (never a silent `format` leak), and `{{`/`}}` stay
literal braces. Domain model attributes are supported, so a template can take
a whole artifact: `"Research {question.text} in {topic}"` and be rendered with
`template.render(question=…, topic=…)`.

Only placeholders that look like identifiers are substituted. Any other brace
run is left **verbatim**, so a literal JSON example in a prompt
(`'Reply with {"name": "..."} for {question}.'`) needs no escaping — the
`{"name": …}` is preserved and `{question}` is filled. A typo like `{questoin}`
is still an identifier and still a missing-variable `KeyError`.

`template.hash` is a stable sha256 of the template text — record it with a
trace (`LLMCall.prompt_hash`, set via `structured_llm(..., prompt_hash=…)`) so
prompt drift is visible instead of a flaky test failure.

`MessagesPrompt` is the same idea for a chat sequence of `(role, template)`
rows — it renders to `list[Message]` ready for an LLM request.

This is deliberately small and dependency-free: it sits between the app's
"domain strings" and `structured_llm`/`LLMAgent.system`, without claiming to be
a general prompting framework.
"""

from __future__ import annotations

import hashlib
import re
import string
from collections.abc import Mapping, Sequence
from typing import Any, cast

from .providers import Message, Role

_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z")

_formatter = string.Formatter()


def _root_fields(template: str) -> frozenset[str]:
    """The top-level variable names referenced by the template."""
    roots: set[str] = set()
    for _, field_name, _, _ in _formatter.parse(template):
        if field_name is None or field_name == "":
            continue
        if _FIELD.match(field_name):
            roots.add(field_name.split(".")[0])
    return frozenset(roots)


def _resolve_field(values: Mapping[str, Any], field_name: str) -> Any:
    parts = field_name.split(".")
    value: Any = values[parts[0]]
    for part in parts[1:]:
        value = value[part] if isinstance(value, Mapping) else getattr(value, part)
    return value


def _convert(value: Any, conversion: str | None) -> Any:
    if conversion == "r":
        return repr(value)
    if conversion == "s":
        return str(value)
    if conversion == "a":
        return ascii(value)
    return value


def _render_safe(template: str, values: Mapping[str, Any]) -> str:
    """Substitutes identifier-shaped `{field}` placeholders, leaves other braces.

    A JSON snippet (`{"name": "..."}`) or an empty `{}` is not a format field as
    far as this renderer is concerned, so it survives verbatim; `{{`/`}}` still
    unescape to literal braces (via `string.Formatter.parse`).
    """
    parts: list[str] = []
    for literal, field_name, format_spec, conversion in _formatter.parse(template):
        parts.append(literal)
        if field_name is None:
            continue
        if not _FIELD.match(field_name):
            # not a template placeholder — put the braces back as written
            parts.append("{" + field_name)
            if conversion:
                parts.append("!" + conversion)
            if format_spec:
                parts.append(":" + format_spec)
            parts.append("}")
            continue
        value = _convert(_resolve_field(values, field_name), conversion)
        parts.append(format(value, format_spec or ""))
    return "".join(parts)


class PromptTemplate:
    """A strict `{var}` template over the values passed to `render`."""

    def __init__(
        self,
        template: str,
        *,
        defaults: Mapping[str, Any] | None = None,
    ):
        if not isinstance(template, str) or not template.strip():
            raise ValueError("prompt template must be a non-empty string")
        self._template = template
        self._defaults = dict(defaults or {})
        self.variables = _root_fields(template)

    @property
    def template(self) -> str:
        return self._template

    @property
    def hash(self) -> str:
        """Stable sha256 of the template text — record it to detect drift."""
        return hashlib.sha256(self._template.encode("utf-8")).hexdigest()

    def render(self, **values: Any) -> str:
        """Fills the placeholders; a missing declared variable is a `KeyError`."""
        merged = {**self._defaults, **values}
        missing = self.variables - merged.keys()
        if missing:
            raise KeyError(f"missing prompt variables: {', '.join(sorted(missing))}")
        try:
            return _render_safe(self._template, merged)
        except (AttributeError, IndexError, KeyError, ValueError) as exc:
            raise ValueError(
                f"failed to render prompt (template {self._template!r}): {exc}"
            ) from exc

    def __repr__(self) -> str:
        return f"PromptTemplate(variables={sorted(self.variables)})"


class MessagesPrompt:
    """A chat prompt: an ordered set of `(role, template)` rows.

    Renders to `list[Message]`; every row sees the same variables and a missing
    variable anywhere is a `KeyError`.
    """

    def __init__(self, messages: Sequence[tuple[str, str]]):
        if not messages:
            raise ValueError(
                "MessagesPrompt requires at least one (role, template) row"
            )
        self._rows: list[tuple[Role, PromptTemplate]] = []
        for role, template in messages:
            if role not in ("system", "user", "assistant", "tool"):
                raise ValueError(f"unknown message role in MessagesPrompt: {role!r}")
            self._rows.append((cast(Role, role), PromptTemplate(template)))
        variables: set[str] = set()
        for _, row in self._rows:
            variables |= set(row.variables)
        self.variables = frozenset(variables)

    @property
    def hash(self) -> str:
        """Stable sha256 of the ordered rows (role + template) — drift detector."""
        joined = "\n".join(
            f"{role}:{template.template}" for role, template in self._rows
        )
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def render(self, **values: Any) -> list[Message]:
        return [
            Message(role=role, content=template.render(**values))
            for role, template in self._rows
        ]

    def __repr__(self) -> str:
        return f"MessagesPrompt(roles={[r for r, _ in self._rows]})"


__all__ = ["MessagesPrompt", "PromptTemplate"]
