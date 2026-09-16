from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Sequence
from typing import Any, Generic, Literal, TypeVar, cast

from pydantic import BaseModel, ValidationError

from .context import Context
from .providers import LLMRequest, Message, Role

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

#: Why `structured_llm` returned None — passed to an `on_error` hook so a
#: caller can tell "no provider configured" (offline/misconfigured) apart
#: from "the provider was called and failed" (network/rate-limit/outage) and
#: "the model replied but not with valid JSON" — all three collapse to the
#: same `None` return (the honest-fallback contract, §67), but a caller that
#: needs to alert on real outages can now distinguish them without changing
#: how it handles the `None`.
StructuredLLMFailure = Literal["no_provider", "provider_error", "parse_error"]
OnStructuredError = Callable[[StructuredLLMFailure, BaseException | None], None]

SYSTEM_STRUCTURED = (
    "You produce structured output. Reply with a single JSON object only, "
    "no commentary, no code fences."
)


def _extract_json(text: str) -> str | None:
    """Extracts the first balanced JSON object from the model text.

    Local models often add text/code fences around JSON — a tolerant
    parser (§67: parsing is not the LLM's job).
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.MULTILINE).rstrip("`").strip()
    try:
        start = text.index("{")
    except ValueError:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_structured(text: str, schema: type[TModel]) -> TModel | None:
    """Tolerant parsing of an LLM response into a pydantic schema."""
    for candidate in (text, _extract_json(text)):
        if not candidate:
            continue
        try:
            return schema.model_validate_json(candidate)
        except ValidationError:
            try:
                return schema.model_validate(json.loads(candidate))
            except (ValueError, ValidationError):
                continue
    return None


def _parse_raw_json(text: str) -> dict[str, Any] | None:
    """Tolerant parsing into a plain `dict`, no pydantic schema — the same
    two-candidate approach `parse_structured` uses (raw text, then the
    extracted JSON substring), for callers with their own JSON Schema
    instead of a pydantic model (`json_schema_llm`)."""
    for candidate in (text, _extract_json(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def structured_llm(
    context: Context,
    *,
    schema: type[TModel],
    system: str = SYSTEM_STRUCTURED,
    user: str,
    attempts: int = 2,
    temperature: float | None = None,
    max_tokens: int | None = None,
    on_error: OnStructuredError | None = None,
) -> TModel | None:
    """Single LLM call against a schema: JSON + tolerant parse + retry.

    Returns `schema` or None (not enough resources / the model did not return valid JSON).
    Deterministic logic (JSON, retry) stays in code; the LLM only reasons (§9, §67).

    `temperature`/`max_tokens`: `None` uses the provider default; pass a value
    for a per-call override.

    `on_error`, if given, is called right before returning None with *why*
    ("no_provider" | "provider_error" | "parse_error") and the exception when
    there is one — for callers that want to distinguish "offline" from "the
    provider is down" (e.g. to alert) without changing how they handle `None`.
    """
    llm = context.resources.llm
    if llm is None:
        if on_error is not None:
            on_error("no_provider", None)
        return None
    instruction = f"Reply with a single JSON object matching this schema:\n{schema.model_json_schema()}"

    def _request(text: str) -> LLMRequest:
        return LLMRequest(
            messages=[
                Message.system(system),
                Message.user(text),
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )

    request = _request(f"{instruction}\n\n{user}")
    total = max(attempts, 1)
    for attempt in range(total):
        try:
            response = await llm.complete(request)
        except Exception as exc:
            logger.warning(
                "structured_llm: provider call failed (attempt %s/%s): %r",
                attempt + 1,
                total,
                exc,
            )
            if attempt + 1 < total:
                await asyncio.sleep(0.4 * (attempt + 1))  # backoff on network failures
                request = _request(
                    f"{instruction}\n\n{user}\n\n"
                    "The previous request failed. Return a single strict JSON object only."
                )
                continue
            if on_error is not None:
                on_error("provider_error", exc)
            return None  # provider/network failed — honest fallback
        parsed = parse_structured(response.text, schema)
        if parsed is not None:
            return parsed
        logger.debug(
            "structured_llm parse failed (attempt %s): %.160r",
            attempt + 1,
            response.text,
        )
        if attempt + 1 < total:
            request = _request(
                f"{instruction}\n\n{user}\n\n"
                "Previous reply was not valid JSON. Return a single strict JSON object only."
            )
    if on_error is not None:
        on_error("parse_error", None)
    return None


async def json_schema_llm(
    context: Context,
    *,
    json_schema: dict[str, Any] | str,
    schema_name: str = "response",
    system: str = SYSTEM_STRUCTURED,
    user: str,
    attempts: int = 2,
    temperature: float | None = None,
    max_tokens: int | None = None,
    on_error: OnStructuredError | None = None,
) -> dict[str, Any] | None:
    """Structured output against *your own* JSON Schema, not one derived
    from a pydantic model — for a schema that must match specific text
    verbatim (a prompt tuned against it) or expresses something pydantic
    can't easily (cross-field constraints, `oneOf`, a hand-maintained
    schema shared with another system). Returns the parsed `dict`, not a
    validated model — nothing here knows your schema's shape well enough
    to build one; validate it yourself downstream if you need to.

    Unlike `structured_llm` (`response_format={"type":"json_object"}` — the
    *weaker* native mode: guarantees valid JSON, not any particular shape),
    this sends the stricter native `{"type":"json_schema",...}` mode, where
    a supporting provider validates the shape itself — still falls back to
    the same tolerant text-parsing `structured_llm` uses if the model (or
    provider) doesn't actually enforce it.

    `json_schema` may be a `dict` or a JSON string (parsed once, up front —
    raises `ValueError` immediately on malformed input, since that's a
    caller bug, not a model failure). Same retry/backoff/honest-`None`
    contract as `structured_llm` — see its docstring for `attempts`/
    `on_error`.
    """
    schema_dict = (
        json.loads(json_schema) if isinstance(json_schema, str) else json_schema
    )
    llm = context.resources.llm
    if llm is None:
        if on_error is not None:
            on_error("no_provider", None)
        return None
    instruction = (
        f"Reply with a single JSON object matching this schema:\n"
        f"{json.dumps(schema_dict)}"
    )
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema_dict, "strict": True},
    }

    def _request(text: str) -> LLMRequest:
        return LLMRequest(
            messages=[Message.system(system), Message.user(text)],
            temperature=temperature,
            response_format=response_format,
            max_tokens=max_tokens,
        )

    request = _request(f"{instruction}\n\n{user}")
    total = max(attempts, 1)
    for attempt in range(total):
        try:
            response = await llm.complete(request)
        except Exception as exc:
            logger.warning(
                "json_schema_llm: provider call failed (attempt %s/%s): %r",
                attempt + 1,
                total,
                exc,
            )
            if attempt + 1 < total:
                await asyncio.sleep(0.4 * (attempt + 1))
                request = _request(
                    f"{instruction}\n\n{user}\n\n"
                    "The previous request failed. Return a single strict JSON object only."
                )
                continue
            if on_error is not None:
                on_error("provider_error", exc)
            return None
        parsed = _parse_raw_json(response.text)
        if parsed is not None:
            return parsed
        logger.debug(
            "json_schema_llm parse failed (attempt %s): %.160r",
            attempt + 1,
            response.text,
        )
        if attempt + 1 < total:
            request = _request(
                f"{instruction}\n\n{user}\n\n"
                "Previous reply was not valid JSON. Return a single strict JSON object only."
            )
    if on_error is not None:
        on_error("parse_error", None)
    return None


async def llm_reply(
    context: Context,
    *,
    system: str = "",
    user: str,
    attempts: int = 2,
    temperature: float | None = None,
    max_tokens: int | None = None,
    on_error: OnStructuredError | None = None,
) -> str | None:
    """A *plain-text* chat completion → `str`, or `None` on an honest failure.

    Convenience over `structured_llm` with a single-text schema: same retries,
    same tolerant parsing, same `None` fallback (no model / provider failure) —
    but no need to declare a one-field body model for free-form replies (§67).

    It sends exactly **one** system message (the wire format is not a place for
    multiple system blocks — extra context belongs in artifacts/views, §28).

    `on_error`: see `structured_llm`.
    """
    body = await structured_llm(
        context,
        schema=_ReplyBody,
        system=system,
        user=user,
        attempts=attempts,
        temperature=temperature,
        max_tokens=max_tokens,
        on_error=on_error,
    )
    return body.text if body is not None else None


async def chat_complete(
    context: Context,
    messages: Sequence[Message | dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
) -> str | None:
    """A raw multi-turn chat completion → text exactly as the model
    returned it, or `None` on an honest failure (§67 — the same no-provider
    / call-failed contract every helper in this module uses).

    Unlike `llm_reply` (exactly one system + one user turn, the reply
    forced through `structured_llm`'s `{"text": "..."}` JSON envelope) or
    `structured_llm`/`json_schema_llm` (schema-validated), this sends
    `messages` exactly as given — your own multi-turn history, built from
    domain artifacts rather than a single system/user pair — and returns
    `response.text` exactly as returned: no envelope, no schema, no retry.
    Pass `response_format` yourself for a provider's native JSON modes;
    nothing here interprets it.

    `messages` entries may be `Message` or a plain
    `{"role": ..., "content": ...}` dict — the shape your own
    message-building code has most likely already produced.
    """
    llm = context.resources.llm
    if llm is None:
        return None
    request = LLMRequest(
        messages=[
            m
            if isinstance(m, Message)
            else Message(role=cast(Role, m["role"]), content=str(m["content"]))
            for m in messages
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
    )
    try:
        response = await llm.complete(request)
    except Exception as exc:
        logger.warning("chat_complete: provider call failed: %r", exc)
        return None
    return response.text


class _ReplyBody(BaseModel):
    text: str


class StructuredLLM(Generic[TModel]):
    """A reusable structured call: a fixed schema + system, varying only `user`.

    Build a role once, use it wherever the same struct is needed:

        extract_facts = StructuredLLM(schema=Facts, system=SYSTEM_EXTRACTOR)
        facts = await extract_facts.call(context, user="Summarize this page: …")

    All deterministic logic (JSON extraction, retries, backoff) is delegated to
    `structured_llm` (§67); the object only carries the fixed parts.
    """

    def __init__(
        self,
        schema: type[TModel],
        *,
        system: str = SYSTEM_STRUCTURED,
        attempts: int = 2,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        on_error: OnStructuredError | None = None,
    ):
        self.schema = schema
        self.system = system
        self.attempts = attempts
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.on_error = on_error

    async def call(self, context: Context, user: str) -> TModel | None:
        return await structured_llm(
            context,
            schema=self.schema,
            system=self.system,
            user=user,
            attempts=self.attempts,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            on_error=self.on_error,
        )
