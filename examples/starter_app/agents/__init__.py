"""Starter-app agents: the chat capability and its reusable pipeline."""

from reactifact import Pipeline
from reactifact.quick import Question

from ..models import ChatReply
from .chat import OFFLINE_REPLY, chat_reply

#: The chat shape: one agent, typed seed and answer.
CHAT_PIPELINE = Pipeline(
    agents=[chat_reply],
    seed_type=Question,
    answer_type=ChatReply,
    name="starter-chat",
)

__all__ = ["CHAT_PIPELINE", "OFFLINE_REPLY", "chat_reply"]
