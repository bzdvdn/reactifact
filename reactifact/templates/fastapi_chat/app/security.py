"""Demo authn + session scoping.

`Depends(get_current_user)` is the authn boundary; replace the static token
with JWT/OIDC in a real app. `scope_session` is *not* auth: it derives the
server-side storage key so a client cannot read another user's session by
reusing its id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from secrets import compare_digest
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.models import User

_bearer = HTTPBearer(auto_error=False)
_CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class SessionRef:
    """Client-facing id and the server-side storage key."""

    client_id: str
    storage_id: str


def scope_session(user: User, client_id: str) -> SessionRef:
    client_id = client_id.strip()
    if not client_id:
        client_id = uuid4().hex
    if not _CLIENT_ID.fullmatch(client_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_id must be 1-64 chars of [A-Za-z0-9._-]",
        )
    return SessionRef(
        client_id=client_id,
        storage_id=f"{user.id}:{client_id}",
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """Demo bearer-token auth; replace with your JWT/OIDC dependency."""
    settings: Settings = request.app.state.settings
    token = settings.auth_token.get_secret_value() if settings.auth_token else None
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="auth is not configured",
        )
    if credentials is None or not compare_digest(credentials.credentials, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return User(
        id=settings.auth_user_id,
        username=settings.auth_username,
        capabilities=settings.capabilities,
    )


__all__ = ["SessionRef", "get_current_user", "scope_session"]
