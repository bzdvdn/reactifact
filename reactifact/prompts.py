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

To put a placeholder *inside* a literal JSON object, double the object's outer
braces: `'{{"answer": "{answer}"}}'` renders to `{"answer": "..."}`. The bare
form `'{"answer": "{answer}"}'` is one literal brace run and is not substituted,
because the renderer cannot tell a JSON key from a format field.

`template.hash` is a stable sha256 of the template text and its defaults —
record it with a trace (`LLMCall.prompt_hash`, set via
`structured_llm(..., prompt_hash=…)`) so prompt drift is visible instead of a
flaky test failure. `template.partial(...)` returns a copy with more defaults;
`required_variables` is what `render` still needs after defaults.

`MessagesPrompt` is the same idea for a chat sequence of `(role, template)`
rows — it renders to `list[Message]` ready for an LLM request. A row may be a
reusable `PromptTemplate`, and `defaults=` supplies shared fallback values
(row defaults win).

`PromptSequence` composes several sections (base role, domain rules, safety)
into one text; `PromptPair` wraps a system/user pair and `.render(...)` returns
a `RenderedPrompt` whose `.as_kwargs()` feeds `structured_llm`/`llm_reply`,
including the prompt hash. The helpers still send one system message; use
`MessagesPrompt` if you genuinely need several wire-level messages.

This is deliberately small and dependency-free: it sits between the app's
"domain strings" and `structured_llm`/`LLMAgent.system`, without claiming to be
a general prompting framework.
"""

from __future__ import annotations

import hashlib
import json
import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast, get_args

from .providers import Message, Role

_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_ROLES = frozenset(get_args(Role))

_formatter = string.Formatter()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _root_fields(template: str) -> frozenset[str]:
    """The top-level variable names referenced by the template."""
    roots: set[str] = set()
    for _, field_name, format_spec, conversion in _formatter.parse(template):
        if field_name is None or field_name == "":
            continue
        if not _FIELD.match(field_name):
            continue
        if conversion not in (None, "s", "r", "a"):
            raise ValueError(f"unknown conversion !{conversion}")
        if format_spec and "{" in format_spec:
            raise ValueError("nested format specs are not supported")
        roots.add(field_name.split(".")[0])
    return frozenset(roots)


def _resolve_field(values: Mapping[str, Any], field_name: str) -> Any:
    try:
        parts = field_name.split(".")
        value: Any = values[parts[0]]
        for part in parts[1:]:
            value = value[part] if isinstance(value, Mapping) else getattr(value, part)
        return value
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot resolve field {field_name!r}: {exc}") from exc


def _convert(value: Any, conversion: str | None) -> Any:
    if conversion == "r":
        return repr(value)
    if conversion == "s":
        return str(value)
    if conversion == "a":
        return ascii(value)
    if conversion is not None:
        raise ValueError(f"unknown conversion !{conversion}")
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
        try:
            self.variables = _root_fields(template)
        except ValueError as exc:
            raise ValueError(f"invalid prompt template {template!r}: {exc}") from exc

    @property
    def template(self) -> str:
        return self._template

    @property
    def defaults(self) -> Mapping[str, Any]:
        """Read-only view of the defaults used by `render`."""
        return MappingProxyType(self._defaults)

    @property
    def required_variables(self) -> frozenset[str]:
        """The variables that `render` still needs after applying defaults."""
        return frozenset(self.variables - self._defaults.keys())

    @property
    def hash(self) -> str:
        """Stable sha256 of the template text and its defaults — drift detector."""
        if not self._defaults:
            return hashlib.sha256(self._template.encode("utf-8")).hexdigest()
        payload = json.dumps(
            {"template": self._template, "defaults": self._defaults},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def partial(self, **values: Any) -> PromptTemplate:
        """Returns a copy with `values` merged into the defaults.

        The original template is unchanged; a key that is not a declared
        variable is rejected instead of silently ignored.
        """
        unknown = set(values) - self.variables
        if unknown:
            raise KeyError(f"unknown prompt variables: {', '.join(sorted(unknown))}")
        return PromptTemplate(self._template, defaults={**self._defaults, **values})

    def render(self, **values: Any) -> str:
        """Fills the placeholders; a missing declared variable is a `KeyError`."""
        merged = {**self._defaults, **values}
        missing = self.variables - merged.keys()
        if missing:
            raise KeyError(f"missing prompt variables: {', '.join(sorted(missing))}")
        try:
            return _render_safe(self._template, merged)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"failed to render prompt (template {self._template!r}): {exc}"
            ) from exc

    def __repr__(self) -> str:
        return f"PromptTemplate(variables={sorted(self.variables)})"


def _with_shared_defaults(
    template: PromptTemplate,
    defaults: Mapping[str, Any],
) -> PromptTemplate:
    """Applies only the shared defaults that this row actually declares."""
    relevant = {
        key: value for key, value in defaults.items() if key in template.variables
    }
    if not relevant:
        return template
    # The row's own defaults win over a shared default.
    return PromptTemplate(
        template.template,
        defaults={**relevant, **template.defaults},
    )


class MessagesPrompt:
    """A chat prompt: an ordered set of `(role, template)` rows.

    A row may be a plain string or a reusable `PromptTemplate`.
    `defaults` provides shared fallback values; they apply only to rows that
    declare the matching variable, and a row's own defaults win. Renders to
    `list[Message]`; every row sees the same variables and a missing variable
    anywhere is a `KeyError`.
    """

    def __init__(
        self,
        messages: Sequence[tuple[str, str | PromptTemplate]],
        *,
        defaults: Mapping[str, Any] | None = None,
    ):
        if not messages:
            raise ValueError(
                "MessagesPrompt requires at least one (role, template) row"
            )
        self._defaults = dict(defaults or {})
        self._rows: list[tuple[Role, PromptTemplate]] = []
        for role, template in messages:
            if role not in _ROLES:
                raise ValueError(f"unknown message role in MessagesPrompt: {role!r}")
            row = (
                template
                if isinstance(template, PromptTemplate)
                else PromptTemplate(template)
            )
            self._rows.append(
                (cast(Role, role), _with_shared_defaults(row, self._defaults))
            )
        variables: set[str] = set()
        for _, row in self._rows:
            variables |= set(row.variables)
        self.variables = frozenset(variables)

    @property
    def defaults(self) -> Mapping[str, Any]:
        """Read-only view of the shared defaults."""
        return MappingProxyType(self._defaults)

    @property
    def required_variables(self) -> frozenset[str]:
        """The variables that `render` still needs after all defaults."""
        required: set[str] = set()
        for _, row in self._rows:
            required |= row.required_variables
        return frozenset(required)

    @property
    def hash(self) -> str:
        """Stable sha256 of the ordered rows and their defaults."""
        payload = json.dumps(
            [[role, template.hash] for role, template in self._rows],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def render(self, **values: Any) -> list[Message]:
        return [
            Message(role=role, content=template.render(**values))
            for role, template in self._rows
        ]

    def __repr__(self) -> str:
        return f"MessagesPrompt(roles={[r for r, _ in self._rows]})"


class PromptSequence:
    """Ordered prompt sections rendered into one text.

    Useful when a system prompt has several parts (base role, domain rules,
    safety policy): each part keeps its own variables/defaults, and the result
    is one string because `structured_llm`/`llm_reply` intentionally send one
    system message. Empty rendered sections are skipped.
    """

    def __init__(
        self,
        parts: Sequence[str | PromptTemplate],
        *,
        separator: str = "\n\n",
        defaults: Mapping[str, Any] | None = None,
    ):
        if not parts:
            raise ValueError("PromptSequence requires at least one part")
        if not isinstance(separator, str):
            raise TypeError("PromptSequence separator must be a string")
        self._separator = separator
        self._defaults = dict(defaults or {})
        self._parts: list[str | PromptTemplate] = []
        for part in parts:
            if isinstance(part, PromptTemplate):
                self._parts.append(_with_shared_defaults(part, self._defaults))
            elif isinstance(part, str):
                self._parts.append(part)
            else:
                raise TypeError(
                    "PromptSequence parts must be strings or PromptTemplate"
                )
        variables: set[str] = set()
        for part in self._parts:
            if isinstance(part, PromptTemplate):
                variables |= set(part.variables)
        self.variables = frozenset(variables)

    @property
    def parts(self) -> tuple[str | PromptTemplate, ...]:
        return tuple(self._parts)

    @property
    def separator(self) -> str:
        return self._separator

    @property
    def defaults(self) -> Mapping[str, Any]:
        """Read-only view of the shared defaults."""
        return MappingProxyType(self._defaults)

    @property
    def required_variables(self) -> frozenset[str]:
        required: set[str] = set()
        for part in self._parts:
            if isinstance(part, PromptTemplate):
                required |= part.required_variables
        return frozenset(required)

    @property
    def hash(self) -> str:
        """Stable sha256 of the ordered parts and separator."""
        payload = json.dumps(
            {
                "separator": self._separator,
                "parts": [
                    part.hash if isinstance(part, PromptTemplate) else _text_hash(part)
                    for part in self._parts
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _with_defaults(self, defaults: Mapping[str, Any]) -> PromptSequence:
        """Applies shared fallback defaults; a part's own defaults win."""
        relevant = {
            key: value for key, value in defaults.items() if key in self.variables
        }
        if not relevant:
            return self
        return PromptSequence(
            self._parts,
            separator=self._separator,
            defaults={**relevant, **self._defaults},
        )

    def partial(self, **values: Any) -> PromptSequence:
        """Returns a copy with `values` applied to the parts that declare them.

        This overrides a part's own defaults, unlike the constructor
        `defaults=` (which is only a shared fallback).
        """
        unknown = set(values) - self.variables
        if unknown:
            raise KeyError(f"unknown prompt variables: {', '.join(sorted(unknown))}")
        parts: list[str | PromptTemplate] = []
        for part in self._parts:
            if isinstance(part, PromptTemplate):
                relevant = {
                    key: value for key, value in values.items() if key in part.variables
                }
                parts.append(part.partial(**relevant) if relevant else part)
            else:
                parts.append(part)
        return PromptSequence(
            parts,
            separator=self._separator,
            defaults=self._defaults,
        )

    def render(self, **values: Any) -> str:
        rendered: list[str] = []
        for index, part in enumerate(self._parts, start=1):
            if isinstance(part, PromptTemplate):
                try:
                    text = part.render(**values)
                except KeyError as exc:
                    raise KeyError(f"part {index}: {exc}") from exc
                except ValueError as exc:
                    raise ValueError(f"part {index}: {exc}") from exc
            else:
                text = part
            if text.strip():
                rendered.append(text)
        return self._separator.join(rendered)

    def __repr__(self) -> str:
        return (
            f"PromptSequence(parts={len(self._parts)}, separator={self._separator!r})"
        )


@dataclass(frozen=True)
class RenderedPrompt:
    """A rendered system/user pair plus the prompt version hash."""

    system: str
    user: str
    prompt_hash: str

    def as_kwargs(self) -> dict[str, str]:
        """Keyword arguments for `structured_llm` / `llm_reply`."""
        return {
            "system": self.system,
            "user": self.user,
            "prompt_hash": self.prompt_hash,
        }


def _prompt_hash(prompt: str | PromptTemplate | PromptSequence) -> str:
    return _text_hash(prompt) if isinstance(prompt, str) else prompt.hash


def _prepare_prompt(
    prompt: str | PromptTemplate | PromptSequence,
    defaults: Mapping[str, Any],
    side: str,
) -> str | PromptTemplate | PromptSequence:
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, PromptTemplate):
        return _with_shared_defaults(prompt, defaults)
    if isinstance(prompt, PromptSequence):
        return prompt._with_defaults(defaults)
    raise TypeError(f"{side} prompt must be a string, PromptTemplate or PromptSequence")


def _render_prompt(
    prompt: str | PromptTemplate | PromptSequence,
    values: Mapping[str, Any],
    side: str,
) -> str:
    if isinstance(prompt, str):
        return prompt
    try:
        return prompt.render(**values)
    except KeyError as exc:
        raise KeyError(f"{side} prompt: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"{side} prompt: {exc}") from exc


def _partial_side(
    prompt: str | PromptTemplate | PromptSequence,
    values: Mapping[str, Any],
) -> str | PromptTemplate | PromptSequence:
    if isinstance(prompt, str):
        return prompt
    relevant = {key: value for key, value in values.items() if key in prompt.variables}
    return prompt.partial(**relevant) if relevant else prompt


class PromptPair:
    """A reusable system/user prompt pair.

    `system`/`user` may be plain strings, `PromptTemplate`s or
    `PromptSequence`s. `defaults` supplies shared fallback values; a part's own
    defaults win, and defaults unused by a side are ignored.
    """

    def __init__(
        self,
        system: str | PromptTemplate | PromptSequence,
        user: str | PromptTemplate | PromptSequence,
        *,
        defaults: Mapping[str, Any] | None = None,
    ):
        self._defaults = dict(defaults or {})
        self._system = _prepare_prompt(system, self._defaults, "system")
        self._user = _prepare_prompt(user, self._defaults, "user")
        variables: set[str] = set()
        for prompt in (self._system, self._user):
            if not isinstance(prompt, str):
                variables |= set(prompt.variables)
        self.variables = frozenset(variables)

    @property
    def defaults(self) -> Mapping[str, Any]:
        """Read-only view of the shared defaults."""
        return MappingProxyType(self._defaults)

    @property
    def required_variables(self) -> frozenset[str]:
        required: set[str] = set()
        for prompt in (self._system, self._user):
            if not isinstance(prompt, str):
                required |= prompt.required_variables
        return frozenset(required)

    @property
    def hash(self) -> str:
        """Stable sha256 of both prompt sides — values are not part of it."""
        payload = json.dumps(
            [_prompt_hash(self._system), _prompt_hash(self._user)],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def partial(self, **values: Any) -> PromptPair:
        """Returns a copy with `values` applied to both prompt sides.

        This overrides a side's own defaults, unlike the constructor
        `defaults=` (which is only a shared fallback).
        """
        unknown = set(values) - self.variables
        if unknown:
            raise KeyError(f"unknown prompt variables: {', '.join(sorted(unknown))}")
        return PromptPair(
            _partial_side(self._system, values),
            _partial_side(self._user, values),
            defaults=self._defaults,
        )

    def render(self, **values: Any) -> RenderedPrompt:
        return RenderedPrompt(
            system=_render_prompt(self._system, values, "system"),
            user=_render_prompt(self._user, values, "user"),
            prompt_hash=self.hash,
        )

    def __repr__(self) -> str:
        return f"PromptPair(system={self._system!r}, user={self._user!r})"


__all__ = [
    "MessagesPrompt",
    "PromptPair",
    "PromptSequence",
    "PromptTemplate",
    "RenderedPrompt",
]
