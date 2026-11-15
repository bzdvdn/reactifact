"""Small JSON-over-HTTP client shared by embedders and remote indexes."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
from collections.abc import Callable
from typing import Any, Literal, overload

import httpx

from .._httpx import LoopBoundClient
from .errors import SourceError

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpClient:
    """A pooled async JSON client with retry/backoff.

    Connection errors and retryable status codes are retried with exponential
    backoff and jitter; `Retry-After` is honored. Every request the knowledge
    pipeline issues is idempotent, so retrying is safe.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_token: str | None = None,
        api_token_env: str | None = None,
        username: str | None = None,
        timeout: float = 30.0,
        retries: int = 3,
        retry_backoff: float = 1.0,
        verify: bool | str = True,
        transport: httpx.AsyncBaseTransport | None = None,
        header_hook: Callable[[str, str, dict[str, Any] | None], dict[str, str]]
        | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.api_token_env = api_token_env
        self.username = username
        self.timeout = timeout
        self.max_retries = retries
        self.retry_backoff = retry_backoff
        self.verify = verify
        self.transport = transport
        self.header_hook = header_hook
        self._http = LoopBoundClient(self._new_client)

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            verify=self.verify,
            transport=self.transport,
        )

    def _resolved_token(self) -> str | None:
        if self.api_token:
            return self.api_token
        if self.api_token_env:
            return os.environ.get(self.api_token_env)
        return None

    def _headers(
        self, method: str, path: str, query: dict[str, Any] | None
    ) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = self._resolved_token()
        if self.api_token_env and not token:
            raise SourceError(f"environment variable {self.api_token_env!r} is not set")
        if token:
            if self.username:
                credentials = base64.b64encode(
                    f"{self.username}:{token}".encode()
                ).decode()
                headers["Authorization"] = f"Basic {credentials}"
            else:
                headers["Authorization"] = f"Bearer {token}"
        if self.header_hook:
            headers.update(self.header_hook(method, path, query))
        return headers

    @overload
    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        ok_status: tuple[int, ...] = (200, 201),
        raw: Literal[False] = False,
    ) -> dict[str, Any]: ...

    @overload
    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        ok_status: tuple[int, ...] = (200, 201),
        raw: Literal[True],
    ) -> bytes: ...

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        ok_status: tuple[int, ...] = (200, 201),
        raw: bool = False,
    ) -> dict[str, Any] | bytes:
        """Perform a request, retrying transient failures."""
        url = self.base_url + path
        client = self._http.get()
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    params=query,
                    json=payload,
                    headers=self._headers(method, path, query),
                )
            except httpx.RequestError as exc:
                if attempt >= self.max_retries:
                    raise SourceError(
                        f"{method} {url} failed after {self.max_retries} retries: {exc}"
                    ) from exc
                await asyncio.sleep(self._delay(attempt, None, None))
                continue

            if response.status_code in _RETRYABLE_STATUS:
                if attempt >= self.max_retries:
                    raise SourceError(
                        f"{method} {url} returned HTTP "
                        f"{response.status_code} after "
                        f"{self.max_retries} retries",
                        status_code=response.status_code,
                    )
                await asyncio.sleep(
                    self._delay(
                        attempt,
                        response.status_code,
                        response.headers.get("Retry-After"),
                    )
                )
                continue

            if response.status_code not in ok_status:
                raise SourceError(
                    f"{method} {url} returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            body = await response.aread()
            if raw:
                return body
            return json.loads(body) if body else {}

        return b"" if raw else {}

    def _delay(
        self, attempt: int, status: int | None, retry_after: str | None
    ) -> float:
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                return 0.0
        base: float = self.retry_backoff * (2**attempt)
        jitter: float = random.uniform(0.0, base * 0.1)
        return base + jitter

    async def aclose(self) -> None:
        """Release the underlying connection pool."""
        await self._http.aclose()


__all__ = ["HttpClient"]
