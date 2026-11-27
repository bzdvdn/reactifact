"""Append-side conversation helpers over one message model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ...artifacts import Artifact
from ...context import Context
from ...effects import Effects, Handle
from ...providers import Message
from ._common import _windowed
from .models import ConversationMessage, MessageBuilder, Role


def _default_build(message_type: type[BaseModel]) -> MessageBuilder:
    """Builds `message_type` passing only the fields it actually declares."""
    fields = set(getattr(message_type, "model_fields", {}))

    def build(role: Role, text: str, turn: int, session_id: str) -> BaseModel:
        kwargs: dict[str, Any] = {"role": role, "text": text}
        if "turn" in fields:
            kwargs["turn"] = turn
        if "session_id" in fields:
            kwargs["session_id"] = session_id
        return message_type(**kwargs)

    return build


@dataclass
class Conversation:
    """A multi-turn conversation over a (possibly custom) message model.

    The model must expose `role` and `text`; `turn`/`session_id` are written
    only if declared. Pass `build=` to construct a model whose shape differs
    (different field names, extra defaults).
    """

    message_type: type[BaseModel] = ConversationMessage
    build: MessageBuilder | None = None
    _builder: MessageBuilder = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._builder = self.build or _default_build(self.message_type)

    def _all(self, ctx: Context) -> list[Artifact[Any]]:
        return sorted(
            ctx.list_artifacts(self.message_type),
            key=lambda a: (getattr(a.data, "turn", 0), a.created_at),
        )

    def turns(self, ctx: Context) -> int:
        """Number of user messages currently recorded (a window size, not a
        monotonic turn index — pruning reduces it)."""
        return sum(1 for a in self._all(ctx) if getattr(a.data, "role", None) == "user")

    def _max_turn(self, ctx: Context) -> int:
        return max((getattr(a.data, "turn", 0) for a in self._all(ctx)), default=0)

    def add(
        self,
        ctx: Context,
        role: Role,
        text: str,
        *,
        session_id: str = "",
        turn: int | None = None,
        id: str | None = None,
    ) -> Artifact[Any]:
        """Records one message (a stable `id` makes a turn idempotent, §42).

        This writes to `ctx` directly: use it from app-side hooks such as
        `Chat(create_message=...)`, not from inside a `Step` (there use
        `add_effect(effects, ...)` or return an `Emit`).

        `turn` defaults to the next index for a `user` message — derived from
        the max recorded turn, so pruning old messages doesn't restart the
        numbering — and to the current one for `assistant`/`system`.
        """
        if turn is None:
            turn = self._max_turn(ctx) + (1 if role == "user" else 0)
        return ctx.create(self._builder(role, text, turn, session_id), id=id)

    def add_effect(
        self,
        effects: Effects,
        role: Role,
        text: str,
        *,
        session_id: str = "",
        turn: int | None = None,
        id: str | None = None,
    ) -> Handle:
        """Records one message in a step's effect slot (see `add`)."""
        if turn is None:
            turn = self._max_turn(effects.context) + (1 if role == "user" else 0)
        return effects.create(self._builder(role, text, turn, session_id), id=id)

    def create_turn(self, ctx: Context, text: str, session_id: str = "") -> str:
        """Creates the user message and returns its id — a `create_message=`
        hook for `Chat`/`run_message`."""
        return self.add(ctx, "user", text, session_id=session_id).id

    def transcript(
        self, ctx: Context, *, window: int | None = None, include_system: bool = True
    ) -> list[Message]:
        """The conversation as provider `Message`s, oldest first (`window` keeps
        the last N raw)."""
        artifacts = self._all(ctx)
        if not include_system:
            artifacts = [
                a for a in artifacts if getattr(a.data, "role", None) != "system"
            ]
        artifacts = _windowed(artifacts, window)
        return [Message(role=a.data.role, content=a.data.text) for a in artifacts]

    def state(self, ctx: Context) -> dict[str, Any]:
        """`Chat(session_state=conversation.state)` history payload."""
        return {
            "messages": [
                {
                    "role": a.data.role,
                    "text": a.data.text,
                    "turn": getattr(a.data, "turn", 0),
                    "at": a.created_at.isoformat(),
                }
                for a in self._all(ctx)
            ]
        }


#: The default conversation (`ConversationMessage`), for the module-level helpers.
_DEFAULT = Conversation()


def add_message(
    ctx: Context,
    role: Role,
    text: str,
    *,
    session_id: str = "",
    turn: int | None = None,
    id: str | None = None,
) -> Artifact[Any]:
    """Records one `ConversationMessage` (see `Conversation.add`)."""
    return _DEFAULT.add(ctx, role, text, session_id=session_id, turn=turn, id=id)


def create_turn(ctx: Context, text: str, session_id: str = "") -> str:
    """Creates the user message of a turn, returns its id — a `create_message=`
    hook (`create_message=create_turn`)."""
    return _DEFAULT.create_turn(ctx, text, session_id)


def turn_count(ctx: Context) -> int:
    """Number of user turns recorded in `ctx` (`ConversationMessage`)."""
    return _DEFAULT.turns(ctx)


def transcript(
    ctx: Context, *, window: int | None = None, include_system: bool = True
) -> list[Message]:
    """The `ConversationMessage` thread as provider `Message`s (see
    `Conversation.transcript`)."""
    return _DEFAULT.transcript(ctx, window=window, include_system=include_system)


def conversation_state(ctx: Context) -> dict[str, Any]:
    """`Chat(session_state=conversation_state)` history payload."""
    return _DEFAULT.state(ctx)
