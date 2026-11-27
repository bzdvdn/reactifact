"""Conversation models and the message-builder type."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]

#: `(role, text, turn, session_id) -> model instance`.
MessageBuilder = Callable[[Role, str, int, str], BaseModel]


class ConversationMessage(BaseModel):
    """The default conversation unit: `role` + `text` + a turn index.

    `turn` is a 1-based user-turn index (an assistant message shares the turn of
    the user message it answers); `session_id` stamps the conversation thread so
    one store can hold several. Substitute your own model via `Conversation`
    when the domain needs more fields.
    """

    role: Role
    text: str
    turn: int = 0
    session_id: str = ""
