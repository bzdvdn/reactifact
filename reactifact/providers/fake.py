"""Deterministic fakes for tests and local runs."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

from .contracts import (
    EmbeddingProvider,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
)


class FakeLLM(LLMProvider):
    def __init__(
        self,
        response: str = "This is a fake response.",
        usage: dict[str, Any] | None = None,
    ):
        self.response = response
        self.usage = usage or {}

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text=self.response, usage=dict(self.usage))

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        yield LLMResponseChunk(text=self.response)
        if self.usage:
            yield LLMResponseChunk(text="", usage=dict(self.usage))


class FakeEmbedder(EmbeddingProvider):
    def __init__(self, dim: int = 8):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            hash_bytes = hashlib.sha256(text.encode()).digest()
            vec = [float(b) / 255.0 for b in hash_bytes[: self.dim]]
            if len(vec) < self.dim:
                vec.extend([0.0] * (self.dim - len(vec)))
            vectors.append(vec)
        return vectors
