"""recipes — a first-class multi-turn conversation over typed artifacts.

LangGraph's `MessagesState` + `add_messages` reducer has no direct reactifact
analog: the framework's unit is a typed artifact, not a message blob. This
recipe fills the common "chat that accumulates turns" shape — an append helper
and a `transcript` renderer to provider `Message`s — so apps stop re-inventing
`Question`/`Answer` plus a hand-rolled prompt builder.

Bring your own message model, or use the shipped `ConversationMessage`:

    from reactifact.recipes.conversation import Conversation, ConversationMessage

    conv = Conversation()                       # the default model
    conv = Conversation(MyMessage)              # or any model with role/text
    conv = Conversation(MyMessage, build=...)   # full control over construction

    conv.create_turn(ctx, "what's the refund policy?", session_id="s1")
    ...agents run...
    conv.add(ctx, "assistant", answer.text, session_id="s1")

    history = conv.transcript(ctx, window=20)   # list[providers.Message]

When the app already has *separate* artifact types for the two sides (the
common case — `Question`/`FinalResponse`, `UserMsg`/`ChatReply`), use the
read-only `Transcript` instead, which merges several types by role:

    from reactifact.recipes.conversation import MessageSpec, Transcript

    view = Transcript([
        MessageSpec(Question, "user"),
        MessageSpec(FinalResponse, "assistant", render=render_answer),
    ])
    history = view.messages(ctx, window=20)

Any message model with `role`/`text` fields works (that is all `transcript`
reads, and all `recipes.memory` needs), so `WindowPruner` /
`RollingDigestSummarizer` and `Chat(memory=ChatMemory(...))` bound a
conversation out of the box. The module-level `add_message`/`create_turn`/
`transcript`/`turn_count`/`conversation_state` are thin defaults over
`ConversationMessage` for the common case.
"""

from __future__ import annotations

from .core import (
    Conversation,
    add_message,
    conversation_state,
    create_turn,
    transcript,
    turn_count,
)
from .models import ConversationMessage, MessageBuilder, Role
from .turn_context import TurnContext, TurnContextStep, TurnFacts, looks_like_follow_up
from .views import MessageSpec, Transcript, TranscriptEntry

__all__ = [
    "Conversation",
    "ConversationMessage",
    "MessageBuilder",
    "MessageSpec",
    "Role",
    "Transcript",
    "TranscriptEntry",
    "TurnContext",
    "TurnContextStep",
    "TurnFacts",
    "add_message",
    "looks_like_follow_up",
    "conversation_state",
    "create_turn",
    "transcript",
    "turn_count",
]
