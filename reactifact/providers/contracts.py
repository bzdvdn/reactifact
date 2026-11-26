"""LLM/embedder provider contracts — clean core, no implementations.

Concrete providers live in the `providers` package (../providers), along with
their env-based factories. Only interfaces live here, so the core does not pull in httpx.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias, TypeVar

from pydantic import BaseModel

Role: TypeAlias = Literal["system", "user", "assistant", "tool"]
TModel = TypeVar("TModel", bound=BaseModel)


@dataclass
class Message:
    """One chat-completion message.

    `role` is a closed set of known roles (a `Literal`) so a typo like
    "assistan" is a `ValueError`, not a silent API failure. Use the factories
    for a clearer call site: `Message.system(…)`, `Message.user(…)`,
    `Message.assistant(…)`, `Message.tool(…)`.
    """

    role: Role
    content: str

    def __post_init__(self) -> None:
        if self.role not in ("system", "user", "assistant", "tool"):
            raise ValueError(f"unknown message role: {self.role!r}")

    @classmethod
    def system(cls, content: str) -> Message:
        """A system/instruction message."""
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        """A user message."""
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> Message:
        """An assistant message (history or few-shot)."""
        return cls(role="assistant", content=content)

    @classmethod
    def tool(cls, content: str) -> Message:
        """A tool-result message."""
        return cls(role="tool", content=content)


@dataclass
class LLMRequest:
    """One chat-completion request; `None` fields use the provider default."""

    messages: list[Message]

    # `None` means "use the provider's default" — the provider decides what to
    # send (or omits the field entirely, letting the API pick). An explicit
    # value here is a per-call override of the provider default.
    temperature: float | None = None
    max_tokens: int | None = None
    stop: list[str] = field(default_factory=list)
    response_format: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    #: Optional fingerprint of the *template* behind this call (e.g.
    #: `PromptTemplate.hash`). Providers ignore it; the tracing `RecordingLLM`
    #: copies it onto the `LLMCall` so prompt drift is visible in a trace.
    prompt_hash: str = ""


@dataclass
class LLMResponse:
    """A completed response: text, raw payload, finish reason and usage."""

    text: str
    raw: Any = None
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponseChunk:
    """One streamed diff; the terminal chunk may carry usage."""

    text: str
    finish_reason: str | None = None
    #: Token usage, when the provider reports it on a streaming chunk (usually
    #: the terminal one). `None` for providers/endpoints that don't — such a
    #: stream is then not counted by a token/cost budget.
    usage: dict[str, Any] | None = None


class LLMProvider(ABC):
    """Chat-completion provider contract (one request in, one response out)."""

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run one non-streaming completion."""
        ...

    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        """Stream one completion as response chunks."""
        ...

    # Deliberately not async: implementations are generators (yield), but the contract is an async iterator.

    async def structured(
        self,
        schema: type[TModel],
        *,
        system: str | None = None,
        user: str,
        **kwargs: Any,
    ) -> TModel | None:
        """Schema-validated completion using this provider.

        The default implementation uses the shared parse/retry/validate policy
        and calls `self.complete`, so tracing/budget wrappers still apply.
        Providers with a native JSON-schema mode may override it.
        """
        from ..structured import SYSTEM_STRUCTURED, structured_llm

        return await structured_llm(
            self,
            schema=schema,
            system=system or SYSTEM_STRUCTURED,
            user=user,
            **kwargs,
        )

    async def structured_json(
        self,
        json_schema: dict[str, Any] | str,
        *,
        schema_name: str = "response",
        system: str | None = None,
        user: str,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Native JSON-schema completion with the shared fallback/retry."""
        from ..structured import SYSTEM_STRUCTURED, json_schema_llm

        return await json_schema_llm(
            self,
            json_schema=json_schema,
            schema_name=schema_name,
            system=system or SYSTEM_STRUCTURED,
            user=user,
            **kwargs,
        )

    async def reply(
        self,
        *,
        system: str = "",
        user: str,
        **kwargs: Any,
    ) -> str | None:
        """A plain-text reply (no caller-side schema)."""
        from ..structured import llm_reply

        return await llm_reply(self, system=system, user=user, **kwargs)

    async def chat(
        self,
        messages: Sequence[Message | dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        prompt_hash: str = "",
    ) -> LLMResponse | None:
        """A raw multi-turn completion, returning the full response or None."""
        from ..structured import chat_complete_full

        return await chat_complete_full(
            self,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            prompt_hash=prompt_hash,
        )


class EmbeddingProvider(ABC):
    """Text-embedding provider contract."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts, preserving input order."""
        ...

    async def embed_one(self, text: str) -> list[float]:
        """Convenience for the common single-text call."""
        vectors = await self.embed([text])
        return vectors[0]


#: Builds the value of an auth header.


def auth_value(api_key: str, scheme: str | None) -> str:
    """Builds the value of an auth header.

    `scheme=None` sends the raw key (Anthropic's `x-api-key`, and other
    `api-key`-style APIs); a custom scheme (Bearer/OAuth/api-key/Token) formats
    it as `f"{scheme} {api_key}"`. The header *name* is provider's choice
    (`Authorization`, `X-Api-Key`, ...) and is not decided here.
    """
    return api_key if scheme is None else f"{scheme} {api_key}"
