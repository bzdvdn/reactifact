"""Artifacts for the A2A example — the server's input, and its answer."""

from pydantic import BaseModel


class Question(BaseModel):
    """What the *client* side seeds locally (before going over A2A)."""

    text: str


class Answer(BaseModel):
    """What the server produces and the client reads back."""

    text: str
