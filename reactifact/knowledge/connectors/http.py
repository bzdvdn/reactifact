"""HTTP connector — explicit URLs or a sitemap, with conditional listing."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from ..errors import SourceError
from ..models import DocumentMeta, RawDocument

T = TypeVar("T")


class HttpConnector:
    """Fetches documents from explicit URLs or a sitemap.

    Listing uses `ETag`/`Last-Modified` as the fingerprint, so incremental runs
    skip unchanged URLs when the server provides validators. Servers without
    validators are re-fetched on every incremental run. Sitemaps are resolved
    recursively one level (sitemap indexes are supported).
    """

    def __init__(
        self,
        urls: str | Sequence[str] = (),
        *,
        sitemap: str | None = None,
        source_id: str = "http",
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        verify: bool | str = True,
        max_concurrency: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not urls and not sitemap:
            raise ValueError("HttpConnector needs urls= or sitemap=")
        self.urls = [urls] if isinstance(urls, str) else list(urls)
        self.sitemap = sitemap
        self.source_id = source_id
        self.headers = {
            "User-Agent": "reactifact-knowledge/0.1",
            **dict(headers or {}),
        }
        self.timeout = timeout
        self.verify = verify
        self.max_concurrency = max(1, max_concurrency)
        self.transport = transport
        self._client = client
        self._owns_client = client is None
        self._resolved: list[str] | None = None

    async def aclose(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_documents(self) -> list[DocumentMeta]:
        """Return URL metadata with `ETag`/`Last-Modified` fingerprints."""
        urls = await self._resolve_urls()
        return await self._gather(urls, self._document_meta)

    async def fetch(self, *, uris: set[str] | None = None) -> list[RawDocument]:
        """Fetch the requested URLs (or all URLs) as raw documents."""
        urls = sorted(uris) if uris is not None else await self._resolve_urls()
        return await self._gather(urls, self._document)

    async def _gather(
        self,
        urls: Sequence[str],
        fetch_one: Callable[[str], Awaitable[T]],
    ) -> list[T]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def one(url: str) -> T:
            async with semaphore:
                return await fetch_one(url)

        return list(await asyncio.gather(*(one(url) for url in urls)))

    async def _document_meta(self, url: str) -> DocumentMeta:
        response = await self._request("HEAD", url)
        if response.status_code in (405, 501):
            response = await self._request("GET", url)
            self._raise_for_status(response, url)
            fingerprint = hashlib.sha256(response.content).hexdigest()
        else:
            self._raise_for_status(response, url)
            fingerprint = (
                response.headers.get("etag")
                or response.headers.get("last-modified")
                or ""
            )
        return DocumentMeta(
            uri=url,
            step_id=url,
            fingerprint=fingerprint,
            content_type=_content_type(response),
        )

    async def _document(self, url: str) -> RawDocument:
        response = await self._request("GET", url)
        self._raise_for_status(response, url)
        title = Path(urlparse(url).path).stem or url
        return RawDocument(
            step_id=url,
            uri=url,
            content_type=_content_type(response) or "text/plain",
            data=response.content,
            metadata={"sourceUrl": url, "title": title},
        )

    async def _request(self, method: str, url: str) -> httpx.Response:
        client = self._get_client()
        try:
            return await client.request(method, url)
        except httpx.HTTPError as exc:
            raise SourceError(f"HTTP {method} {url} failed: {exc}") from exc

    async def _resolve_urls(self) -> list[str]:
        if self._resolved is not None:
            return self._resolved
        urls = list(dict.fromkeys(self.urls))
        if self.sitemap is not None:
            urls.extend(await self._sitemap_urls(self.sitemap, depth=0))
        self._resolved = list(dict.fromkeys(urls))
        return self._resolved

    async def _sitemap_urls(self, url: str, *, depth: int) -> list[str]:
        response = await self._request("GET", url)
        self._raise_for_status(response, url)
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise SourceError(f"invalid sitemap {url!r}: {exc}") from exc
        locations = [
            element.text.strip()
            for element in root.iter()
            if element.tag.endswith("loc") and element.text and element.text.strip()
        ]
        if root.tag.endswith("sitemapindex") and depth < 1:
            nested: list[str] = []
            for location in locations:
                nested.extend(await self._sitemap_urls(location, depth=depth + 1))
            return nested
        return locations

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                verify=self.verify,
                headers=self.headers,
                transport=self.transport,
                follow_redirects=True,
            )
        return self._client

    @staticmethod
    def _raise_for_status(response: httpx.Response, url: str) -> None:
        if response.status_code >= 400:
            raise SourceError(
                f"HTTP {response.request.method} {url} returned {response.status_code}",
                status_code=response.status_code,
            )


def _content_type(response: httpx.Response) -> str | None:
    value = response.headers.get("content-type", "")
    return value.split(";", 1)[0].strip() or None


__all__ = ["HttpConnector"]
