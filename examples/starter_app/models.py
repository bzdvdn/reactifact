"""Wire + artifact models for the starter app."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AskMode = Literal["agent", "rag", "tools"]


class AskRequest(BaseModel):
    mode: AskMode = "rag"
    question: str


class AskResponse(BaseModel):
    mode: AskMode
    text: str
    sources: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class AnswerBody(BaseModel):
    """Schema for the `agent` quick case."""

    text: str


class ChatReply(BaseModel):
    """The artifact the chat produce writes; the assistant returns its text."""

    text: str
