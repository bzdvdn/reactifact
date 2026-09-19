"""LLM/embedder provider contracts — clean core, no implementations.

Concrete providers live in the `providers` package (../providers), along with
their env-based factories. Only interfaces live here, so the core does not pull in httpx.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

Role: TypeAlias = Literal["system", "user", "assistant", "tool"]


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
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> Message:
        return cls(role="assistant", content=content)

    @classmethod
    def tool(cls, content: str) -> Message:
        return cls(role="tool", content=content)


@dataclass
class LLMRequest:
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
    text: str
    raw: Any = None
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponseChunk:
    text: str
    finish_reason: str | None = None


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    # Deliberately not async: implementations are generators (yield), but the contract is an async iterator.
    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]: ...


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


#: Builds the value of an auth header.


def auth_value(api_key: str, scheme: str | None) -> str:
    """Builds the value of an auth header.

    `scheme=None` sends the raw key (Anthropic's `x-api-key`, and other
    `api-key`-style APIs); a custom scheme (Bearer/OAuth/api-key/Token) formats
    it as `f"{scheme} {api_key}"`. The header *name* is provider's choice
    (`Authorization`, `X-Api-Key`, ...) and is not decided here.
    """
    return api_key if scheme is None else f"{scheme} {api_key}"
