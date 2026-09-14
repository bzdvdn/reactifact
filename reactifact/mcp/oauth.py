"""OAuth 2.1 for the MCP client: machine-to-machine auth (client_credentials)
for MCP servers that require it.

Scoped to `client_credentials` only — the flow that needs no browser or
human consent, which covers an agent authenticating to a server on its own
behalf. The authorization-code flow (a person granting consent through a
browser redirect) needs a `redirect_handler`/`callback_handler` wired to
whatever hosts the app (CLI, a script, a web app), which is host-specific
plumbing outside a headless library's scope; use
`mcp.client.auth.OAuthClientProvider` directly if you need it.
"""

from __future__ import annotations

from typing import Any, Literal

from .._extras import require_extra


class InMemoryTokenStorage:
    """The MCP SDK's `TokenStorage` protocol, held in process memory only.

    Tokens (and refreshes) live only as long as this object — nothing is
    written to disk. Fine for a long-running process reusing one provider
    across calls; pass your own `TokenStorage` implementation instead if you
    need tokens to survive a restart.
    """

    def __init__(self) -> None:
        self._tokens: Any = None
        self._client_info: Any = None

    async def get_tokens(self) -> Any:
        return self._tokens

    async def set_tokens(self, tokens: Any) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> Any:
        return self._client_info

    async def set_client_info(self, client_info: Any) -> None:
        self._client_info = client_info


def oauth_client_credentials(
    server_url: str,
    *,
    client_id: str,
    client_secret: str,
    issuer: str,
    scope: str | None = None,
    token_endpoint_auth_method: Literal[
        "client_secret_basic", "client_secret_post"
    ] = "client_secret_basic",
    storage: Any = None,
) -> Any:
    """Builds an `httpx2.Auth` that authenticates to `server_url` via OAuth's
    `client_credentials` grant — pass it as `auth=` to `mcp_http_tools`.

    `issuer` names the authorization server `client_id`/`client_secret`
    belong to: the token request is only ever built from that server's
    discovered metadata, so a compromised or misconfigured MCP server can't
    redirect the credential exchange elsewhere. Requires the `mcp` extra.

        from reactifact.mcp import mcp_http_tools, oauth_client_credentials

        auth = oauth_client_credentials(
            "https://mcp.example.com",
            client_id="...",
            client_secret="...",
            issuer="https://auth.example.com",
        )
        async with mcp_http_tools("https://mcp.example.com", auth=auth) as tools:
            ...

    `storage` defaults to a fresh `InMemoryTokenStorage()` (tokens live only
    for this process) — pass your own `TokenStorage` to persist/reuse tokens
    across runs.
    """
    require_extra("oauth_client_credentials", "mcp", "mcp")
    from mcp.client.auth.extensions.client_credentials import (
        ClientCredentialsOAuthProvider,
    )

    return ClientCredentialsOAuthProvider(
        server_url,
        storage=storage if storage is not None else InMemoryTokenStorage(),
        client_id=client_id,
        client_secret=client_secret,
        token_endpoint_auth_method=token_endpoint_auth_method,
        scope=scope,
        issuer=issuer,
    )
