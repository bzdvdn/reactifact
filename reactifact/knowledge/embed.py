"""Embedding: `Chunk` -> vector, with caching and provider adapters."""

from __future__ import annotations

import asyncio
import hashlib
import re
import shelve
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

from ..providers import EmbeddingProvider
from ._closers import aclose_of, close_of
from .models import Chunk


class Embedder(Protocol):
    """Maps chunks to fixed-size vectors."""

    signature: str

    async def embed(self, chunk: Chunk) -> list[float]:
        """Embed a single chunk."""
        ...

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        """Embed chunks, preserving input order."""
        ...


class HashEmbedder:
    """Deterministic placeholder embedder (no network, no API key).

    Produces a fixed-size vector from token hashes. Useful for tests and for
    running a pipeline end-to-end before a real embedding endpoint exists.
    """

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions
        self.signature = f"default:{dimensions}"

    async def embed(self, chunk: Chunk) -> list[float]:
        return (await self.embed_batch([chunk]))[0]

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for chunk in chunks:
            vector = [0.0] * self.dimensions
            for token in _tokens(chunk.text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "little") % self.dimensions
                sign = 1.0 if digest[0] % 2 == 0 else -1.0
                vector[index] += sign
            norm = sum(value * value for value in vector) ** 0.5
            if norm > 0.0:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


class ProviderEmbedder:
    """Adapts a core `reactifact.EmbeddingProvider` to the `Embedder` protocol.

    `KnowledgePipeline` takes collaborators explicitly; for a normal app the
    embedder is the one already wired into `RuntimeResources`:

        resources = RuntimeResources(embedder=embedder_from_env())
        pipeline = KnowledgePipeline(
            connector,
            embedder=ProviderEmbedder(resources.embedder),
        )

    Batching and concurrency live here, so provider implementations stay the
    small `embed(texts)` contract the rest of reactifact already uses.
    """

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        signature: str | None = None,
        batch_size: int = 32,
        max_concurrency: int = 4,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.provider = provider
        self.batch_size = batch_size
        self.max_concurrency = max_concurrency
        model = getattr(provider, "model", "")
        self.signature = signature or (
            f"{type(provider).__name__}:{model}" if model else type(provider).__name__
        )

    async def embed(self, chunk: Chunk) -> list[float]:
        return (await self.embed_batch([chunk]))[0]

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        if not chunks:
            return []
        batches = [
            chunks[start : start + self.batch_size]
            for start in range(0, len(chunks), self.batch_size)
        ]
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _embed_one(batch: Sequence[Chunk]) -> list[list[float]]:
            async with semaphore:
                return await self.provider.embed([chunk.text for chunk in batch])

        results = await asyncio.gather(*(_embed_one(batch) for batch in batches))
        vectors: list[list[float]] = []
        for result in results:
            vectors.extend(result)
        return vectors

    async def aclose(self) -> None:
        closer = aclose_of(self.provider)
        if closer is not None:
            await closer()


class EmbeddingCache(Protocol):
    """Persists vectors by opaque string key."""

    def get(self, key: str) -> list[float] | None: ...

    def set(self, key: str, vector: list[float]) -> None: ...


class InMemoryEmbeddingCache:
    """Per-process embedding cache."""

    def __init__(self) -> None:
        self._store: dict[str, list[float]] = {}

    def get(self, key: str) -> list[float] | None:
        return self._store.get(key)

    def set(self, key: str, vector: list[float]) -> None:
        self._store[key] = vector


class FileEmbeddingCache:
    """Persistent embedding cache backed by the stdlib `shelve` module."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._shelf: shelve.Shelf[Any] | None = None

    def _open(self) -> shelve.Shelf[Any]:
        if self._shelf is None:
            self._shelf = shelve.open(  # noqa: SIM115
                self.path, flag="c", writeback=False
            )
        return self._shelf

    def get(self, key: str) -> list[float] | None:
        with _CACHE_GUARD:
            value = self._open().get(key)
            return None if value is None else list(value)

    def set(self, key: str, vector: list[float]) -> None:
        with _CACHE_GUARD:
            shelf = self._open()
            shelf[key] = vector
            shelf.sync()

    def close(self) -> None:
        with _CACHE_GUARD:
            if self._shelf is not None:
                self._shelf.close()
                self._shelf = None


class CachingEmbedder:
    """Wraps an `Embedder`, serving known texts from a cache."""

    def __init__(
        self,
        delegate: Embedder,
        *,
        cache: EmbeddingCache | None = None,
    ) -> None:
        self.delegate = delegate
        self.cache = cache or InMemoryEmbeddingCache()
        self.cache_hits = 0
        self.cache_misses = 0
        self.signature = delegate.signature

    async def embed(self, chunk: Chunk) -> list[float]:
        return (await self.embed_batch([chunk]))[0]

    async def embed_batch(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        keys = [cache_key(self.signature, chunk.text) for chunk in chunks]
        vectors: list[list[float] | None] = [None] * len(chunks)
        missing: list[int] = []
        for index, key in enumerate(keys):
            cached = self.cache.get(key)
            if cached is None:
                self.cache_misses += 1
                missing.append(index)
            else:
                self.cache_hits += 1
                vectors[index] = cached
        if missing:
            computed = await self.delegate.embed_batch(
                [chunks[index] for index in missing]
            )
            for position, vector in zip(missing, computed, strict=True):
                vectors[position] = vector
                self.cache.set(keys[position], vector)
        return [cast(list[float], vector) for vector in vectors]

    def close(self) -> None:
        closer = close_of(self.cache)
        if closer is not None:
            closer()

    async def aclose(self) -> None:
        self.close()
        delegate_closer = aclose_of(self.delegate)
        if delegate_closer is not None:
            await delegate_closer()


def cache_key(signature: str, text: str) -> str:
    """Stable cache key: model signature + content hash."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{signature}:{digest}"


_CACHE_GUARD = threading.Lock()
_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


__all__ = [
    "CachingEmbedder",
    "Embedder",
    "EmbeddingCache",
    "FileEmbeddingCache",
    "HashEmbedder",
    "InMemoryEmbeddingCache",
    "ProviderEmbedder",
    "cache_key",
]
