"""Domain artifacts and the app's identity model."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserQuery(BaseModel):
    """One user turn; the chat layer stamps `session_id`."""

    text: str
    session_id: str = ""


class Answer(BaseModel):
    """The assistant's terminal artifact."""

    query_id: str = ""
    text: str


class AnswerBody(BaseModel):
    """The LLM's structured reply (the app owns the id/query fields)."""

    text: str


class User(BaseModel):
    """Authenticated identity (replace the demo token with JWT/OIDC)."""

    id: str
    username: str
    capabilities: tuple[str, ...] = Field(default_factory=lambda: ("user",))
