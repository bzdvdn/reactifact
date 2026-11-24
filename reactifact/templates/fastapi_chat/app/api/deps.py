"""FastAPI dependencies shared by the routes."""

from __future__ import annotations

from typing import cast

from fastapi import Request
from reactifact import Chat

from app.security import SessionRef, get_current_user, scope_session

__all__ = [
    "SessionRef",
    "get_assistant",
    "get_current_user",
    "scope_session",
]


def get_assistant(request: Request) -> Chat:
    return cast(Chat, request.app.state.assistant)
