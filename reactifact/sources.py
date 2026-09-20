from __future__ import annotations

import asyncio
import csv
import hashlib
import html as _html
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ._httpx import LoopBoundClient


class SourceRef(BaseModel):
    """A reference to an external object that can be saved as an artifact.

    May be a search result: then score/title/excerpt are filled in
    (retrieval as a source capability, §8). Materialization is implemented
    through `Source.resolve`.
    """

    source_id: str
    locator: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None
    title: str = ""
    excerpt: str = ""
    query_id: str = ""

    def stable_id(self) -> str:
        raw = f"{self.source_id}:{self.locator}"
        return hashlib.sha1(raw.encode()).hexdigest()


class Source(ABC):
    def __init__(self, source_id: str):
        self.source_id = source_id
        # Sorting hint for aggregating search: preferred sources
        # (e.g., vector RAG) are polled first, the rest fill in.
        self.preferred: bool = False

    def search(self, query: str, limit: int = 10) -> list[SourceRef]:
        """Finds references to relevant content (by default — cannot do it).

        The agent must not know the search mechanics: vector/keywords/SQL/CQL
        — that is the source's choice (§8). Sources without search simply
        return an empty list and stay available via resolve.
        """
        return []

    async def asearch(self, query: str, limit: int = 10) -> list[SourceRef]:
        """Asynchronous search (embeddings, API). Default — synchronous `search`.

        Vector sources cannot compute the query embedding synchronously,
        so the aggregator (`ScoutSources`) uses `asearch`.
        """
        return self.search(query, limit)

    @abstractmethod
    async def resolve(self, ref: SourceRef) -> Any:
        """Resolves a reference into materialized data (e.g., text, structure)."""
        ...


def _token_overlap_score(text: str, query: str) -> float:
    """Core neutral keyword matcher (§67), without linguistic morphology.

    Exact term overlap (unicode alphanumeric words). Language normalization
    (stemming, synonyms) is the app's concern: its scorer is passed
    to `FileSystemSource(scorer=...)` or `CSVSource(scorer=...)`.
    """
    words = set(re.findall(r"\w{2,}", text.casefold()))
    query_terms = [
        t for t in re.findall(r"\w{2,}", query.casefold()) if not t.isdigit()
    ]
    if not query_terms:
        return 0.0
    hits = sum(1 for term in set(query_terms) if term in words)
    return hits / len(query_terms)


class FileSystemSource(Source):
    """Filesystem source with keyword search (deterministic path, §67).

    Search is pure lexical over .md/.txt content; no embedders.
    """

    def __init__(
        self,
        root: str,
        source_id: str = "filesystem",
        extensions: tuple[str, ...] = (".md", ".txt"),
        scorer: Callable[[str, str], float] | None = None,
    ):
        super().__init__(source_id=source_id)
        self.root = Path(root)
        self.extensions = extensions
        self.scorer = scorer or _token_overlap_score

    def search(self, query: str, limit: int = 10) -> list[SourceRef]:
        if not self.root.exists():
            return []
        results: list[SourceRef] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix not in self.extensions:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            score = self.scorer(content, query)
            if score <= 0:
                continue
            locator = str(path.relative_to(self.root))
            excerpt = " ".join(content.split())[:200]
            results.append(
                SourceRef(
                    source_id=self.source_id,
                    locator=locator,
                    score=round(score, 3),
                    title=path.name,
                    excerpt=excerpt,
                )
            )
        results.sort(key=lambda r: r.score or 0.0, reverse=True)
        return results[:limit]

    async def asearch(self, query: str, limit: int = 10) -> list[SourceRef]:
        """Offloads the directory scan to a worker thread (§ reactifact.checkpoints
        has the same pattern): `search()` walks and reads every matching file
        under `root` synchronously, which would otherwise stall the runtime's
        event loop — and every other agent running concurrently with it — for
        as long as the scan takes."""
        return await asyncio.to_thread(self.search, query, limit)

    async def resolve(self, ref: SourceRef) -> str:
        full_path = self.root / ref.locator
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {full_path}")
        return await asyncio.to_thread(full_path.read_text, encoding="utf-8")


def _chunk_text(text: str, size: int) -> list[str]:
    """Splits text into chunks by paragraphs, gluing up to `size` characters."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for paragraph in paragraphs:
        if len(buf) + len(paragraph) + 1 <= size:
            buf = f"{buf}\n{paragraph}".strip()
        else:
            if buf:
                chunks.append(buf)
            buf = paragraph
    if buf:
        chunks.append(buf)
    return chunks


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot: float = sum(x * y for x, y in zip(a, b, strict=False))
    na: float = sum(x * x for x in a) ** 0.5
    nb: float = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class EmbeddingSource(Source):
    """Vector source (RAG): embeddings are the search strategy (§8, §9).

    Indexes files (chunks + vectors) lazily on the first search; `asearch`
    embeds the query and ranks by cosine similarity. Since embedding is
    async, synchronous `search` is not supported — the aggregator must
    call `asearch`. Semantic search is preferred over keyword sources,
    hence `preferred=True` (scout polls it first).

    The index is built once and kept forever — a file changed under `root`
    after the first search stays invisible until you call `invalidate()`.
    """

    def __init__(
        self,
        root: str,
        source_id: str = "rag",
        embedder: Any | None = None,
        extensions: tuple[str, ...] = (".md", ".txt"),
        chunk_size: int = 1200,
        score_threshold: float = 0.15,
    ):
        super().__init__(source_id=source_id)
        if embedder is None:
            raise ValueError("EmbeddingSource requires an embedder")
        self.preferred = True  # RAG is polled first (RAG-first)
        self.root = Path(root)
        self.embedder = embedder
        self.extensions = extensions
        self.chunk_size = chunk_size
        self.score_threshold = score_threshold
        self._index: list[tuple[str, str, list[float]]] | None = None

    def search(self, query: str, limit: int = 10) -> list[SourceRef]:
        raise NotImplementedError(
            "EmbeddingSource requires async asearch (embedding is async)"
        )

    def _scan_chunks_sync(self) -> list[tuple[str, str]]:
        if not self.root.exists():
            return []
        chunks: list[tuple[str, str]] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix not in self.extensions:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for chunk in _chunk_text(content, self.chunk_size):
                chunks.append((str(path.relative_to(self.root)), chunk))
        return chunks

    async def _ensure_index(self) -> None:
        if self._index is not None:
            return
        # Directory walk + file reads are blocking — run them off the event
        # loop (same reasoning as FileSystemSource.asearch); the embed() call
        # below is presumably already async-native I/O, so it stays direct.
        chunks = await asyncio.to_thread(self._scan_chunks_sync)
        if not chunks:
            self._index = []
            return
        vectors = await self.embedder.embed([text for _, text in chunks])
        self._index = [
            (loc, text, vec) for (loc, text), vec in zip(chunks, vectors, strict=False)
        ]

    def invalidate(self) -> None:
        """Drops the cached index so the next search rebuilds it from disk.

        `_ensure_index()` only ever builds the index once, lazily, on the
        first search (see the class docstring) — nothing detects that files
        under `root` changed since. Call this yourself (e.g. after a known
        write, or on a periodic timer) to pick up changes.
        """
        self._index = None

    async def asearch(self, query: str, limit: int = 10) -> list[SourceRef]:
        await self._ensure_index()
        if not self._index:
            return []
        qvec = (await self.embedder.embed([query]))[0]
        best: dict[str, tuple[float, str]] = {}
        for loc, text, vec in self._index:
            score = _cosine(qvec, vec)
            if score < self.score_threshold:
                continue
            if loc not in best or score > best[loc][0]:
                best[loc] = (score, text)
        ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
        return [
            SourceRef(
                source_id=self.source_id,
                locator=loc,
                score=round(score, 3),
                title=Path(loc).name,
                excerpt=text[:200],
            )
            for loc, (score, text) in ranked[:limit]
        ]

    async def resolve(self, ref: SourceRef) -> str:
        full_path = self.root / ref.locator
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {full_path}")
        return await asyncio.to_thread(full_path.read_text, encoding="utf-8")


class CSVSource(Source):
    """CSV source: structured data, not text (§29).

    Search (`search`) is keywords over the file name, headers and first
    rows; `resolve` returns `{"columns": [...], "rows": [[...]]}` —
    the structure is preserved (schema + rows), the agent computes over it
    deterministically (§67). Refs are marked `metadata.structured` so the
    materializer knows: it is a table, not text (§64).

    Matching is exact term overlap by default (no stemming/plurals — see
    `_token_overlap_score`); pass `scorer=` (same signature as
    `FileSystemSource`) for language-aware matching, e.g. headers like
    `cost_usd` won't match a query for "costs" without one.
    """

    def __init__(
        self,
        root: str,
        source_id: str = "csv",
        extensions: tuple[str, ...] = (".csv",),
        scorer: Callable[[str, str], float] | None = None,
    ):
        super().__init__(source_id=source_id)
        self.root = Path(root)
        self.extensions = extensions
        self.scorer = scorer or _token_overlap_score

    @staticmethod
    def _read(path: Path) -> list[list[str]]:
        with path.open(newline="", encoding="utf-8") as fh:
            return [[(cell or "").strip() for cell in row] for row in csv.reader(fh)]

    def search(self, query: str, limit: int = 10) -> list[SourceRef]:
        if not self.root.exists():
            return []
        results: list[SourceRef] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix not in self.extensions:
                continue
            try:
                rows = self._read(path)
            except (OSError, csv.Error):
                continue
            if not rows:
                continue
            header = rows[0]
            sample = rows[1 : 1 + _CSV_SEARCH_SAMPLE]
            flat = (
                " ".join(header)
                + " "
                + " ".join(" ".join(cell for cell in row) for row in sample)
            )
            score = self.scorer(f"{path.name} {flat}", query)
            if score <= 0:
                continue
            excerpt = " | ".join(sample[0]) if sample else " | ".join(header)
            results.append(
                SourceRef(
                    source_id=self.source_id,
                    locator=str(path.relative_to(self.root)),
                    metadata={"structured": True},
                    score=round(score, 3),
                    title=path.name,
                    excerpt=excerpt[:200],
                )
            )
        results.sort(key=lambda r: r.score or 0.0, reverse=True)
        return results[:limit]

    async def asearch(self, query: str, limit: int = 10) -> list[SourceRef]:
        """Offloads the directory scan to a worker thread — see
        `FileSystemSource.asearch` for why."""
        return await asyncio.to_thread(self.search, query, limit)

    async def resolve(self, ref: SourceRef) -> dict[str, Any]:
        full_path = self.root / ref.locator
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {full_path}")
        rows = await asyncio.to_thread(self._read, full_path)
        if not rows:
            return {"columns": [], "rows": []}
        return {"columns": rows[0], "rows": rows[1:]}


_CSV_SEARCH_SAMPLE = 5


def _html_to_text(html: str) -> str:
    """Strips scripts/styles/boilerplate/tags from an HTML document, then
    collapses whitespace into readable paragraphs.

    Best-effort and dependency-free (regex, not a DOM parser) — good enough
    for typical page markup, where real sites (Wikipedia, docs sites) use
    `<nav>`/`<header>`/`<footer>`/`<aside>` as flat, non-nested landmarks; a
    same-tag region nested inside itself may not fully strip. Without this,
    boilerplate ("Jump to content", cookie banners, site nav) can outrank
    real content in the naive `[:200]`-style offline fallback excerpt.
    """
    without_comments = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    without_boilerplate = re.sub(
        r"<(script|style|noscript|nav|header|footer|aside)[^>]*>.*?</\1>",
        " ",
        without_comments,
        flags=re.S | re.I,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_boilerplate)
    text = _html.unescape(without_tags)
    paragraphs = [p.strip() for p in re.split(r"\s*\n\s*", text) if p.strip()]
    return "\n".join(paragraphs).strip()


class WebSource(Source):
    """HTTP source (web pages) with lazy fetching (§6, §32).

    Register URLs up front (`add_url`) — nothing is fetched until a search or
    resolve actually needs the page. `asearch` lazily fetches the registered
    pages, ranks them by keyword overlap, and returns `SourceRef`s whose
    `locator` is the URL. `resolve` downloads the page and returns its text.
    This keeps "go somewhere for the data" a Source capability, not core agent
    logic. `transport` is injectable for hermetic tests (MockTransport).
    """

    DEFAULT_UA = "reactifact-agent (+https://github.com/bzdvdn/reactifact)"

    def __init__(
        self,
        urls: list[str] | None = None,
        source_id: str = "web",
        timeout: float = 15.0,
        transport: Any | None = None,
        user_agent: str = DEFAULT_UA,
    ):
        super().__init__(source_id=source_id)
        self._urls: list[tuple[str, str]] = []
        self._cache: dict[str, tuple[str, str]] = {}  # url -> (title, text)
        self._timeout = timeout
        self._transport = transport
        self._headers = {"User-Agent": user_agent, "Accept": "text/html,*/*"}
        self._http = LoopBoundClient(self._build_client)
        if urls:
            for url in urls:
                self.add_url(url)

    def add_url(self, url: str, title: str = "") -> None:
        """Registers a URL to consider; the page is fetched lazily."""
        if not any(u == url for u, _ in self._urls):
            self._urls.append((url, title))

    @property
    def urls(self) -> list[tuple[str, str]]:
        """Registered (url, title) pairs — never fetched by just listing them."""
        return list(self._urls)

    def _build_client(self) -> Any:
        import httpx

        return httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, headers=self._headers
        )

    def _get_client(self) -> Any:
        return self._http.get()

    async def aclose(self) -> None:
        """Closes the HTTP client (if one was created) on the current loop."""
        await self._http.aclose()

    async def _fetch(self, url: str) -> str:
        response = await self._get_client().get(url)
        response.raise_for_status()
        return str(response.text)

    async def _ensure(self, url: str, title: str = "") -> tuple[str, str]:
        """Fetches and caches (title, text) for a URL if it is not cached yet."""
        if url not in self._cache:
            html_text = await self._fetch(url)
            page_title = title
            if not page_title:
                match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.S | re.I)
                page_title = match.group(1).strip() if match else url
            self._cache[url] = (
                page_title,
                _html_to_text(html_text),
            )
        return self._cache[url]

    def search(self, query: str, limit: int = 10) -> list[SourceRef]:
        """Keyword search over already-cached pages (§8 keyword, run on demand).

        Pages that have not been fetched yet are invisible here — call
        `asearch` for a live pass over the registered URLs.
        """
        results: list[SourceRef] = []
        for url, title in self._urls:
            entry = self._cache.get(url)
            if entry is None:
                continue
            page_title, text = entry
            score = _token_overlap_score(text, query)
            if score <= 0:
                continue
            results.append(
                SourceRef(
                    source_id=self.source_id,
                    locator=url,
                    score=round(score, 3),
                    title=page_title or title,
                    excerpt=text[:220],
                )
            )
        results.sort(key=lambda r: r.score or 0.0, reverse=True)
        return results[:limit]

    async def asearch(self, query: str, limit: int = 10) -> list[SourceRef]:
        """Lazily fetches all registered URLs, then ranks them by relevance."""
        if self._urls:
            await asyncio.gather(*(self._ensure(url, t) for url, t in self._urls))
        return self.search(query, limit)

    async def resolve(self, ref: SourceRef) -> str:
        _, text = await self._ensure(ref.locator, ref.title)
        return text
