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
`RollingDigestSummarizer` and `ChatAssistant(memory=ChatMemory(...))` bound a
conversation out of the box. The module-level `add_message`/`create_turn`/
`transcript`/`turn_count`/`conversation_state` are thin defaults over
`ConversationMessage` for the common case.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from ..artifacts import Artifact
from ..context import Context
from ..providers import Message

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


@dataclass(frozen=True)
class MessageSpec:
    """How one artifact type maps into a `Transcript`.

    `render` turns the artifact's data into message text (default: its `.text`
    field, if any). Use it for an assistant type whose payload isn't plain text
    (e.g. a contract JSON rendered to prose).
    """

    type: type[BaseModel]
    role: Role
    render: Callable[[Any], str] | None = None

    def text_of(self, data: Any) -> str:
        if self.render is not None:
            return self.render(data)
        text = getattr(data, "text", None)
        return text if isinstance(text, str) else str(data)


@dataclass
class Transcript:
    """A read-only view of a conversation built from *several* artifact types.

    Where `Conversation` owns one message model you append to, `Transcript`
    renders an app's existing typed turns — e.g. `Question` (user) +
    `FinalResponse` (assistant) — into provider `Message`s, leaving the
    artifacts untouched. It removes the merge/sort/window/map boilerplate apps
    otherwise repeat (a role per type, a renderer for a non-text assistant
    side), and its `state` is a `ChatAssistant(session_state=…)` payload.
    """

    specs: list[MessageSpec]
    order_key: Callable[[Artifact[Any]], Any] | None = None

    def _entries(self, ctx: Context) -> list[tuple[Any, MessageSpec, Artifact[Any]]]:
        key = self.order_key or (lambda a: a.created_at)
        entries: list[tuple[Any, MessageSpec, Artifact[Any]]] = [
            (key(a), spec, a)
            for spec in self.specs
            for a in ctx.list_artifacts(spec.type)
        ]
        entries.sort(key=lambda e: e[0])
        return entries

    def messages(
        self, ctx: Context, *, window: int | None = None, include_system: bool = True
    ) -> list[Message]:
        """The turns as provider `Message`s, oldest first (`window` keeps the
        last N)."""
        entries = self._entries(ctx)
        if not include_system:
            entries = [e for e in entries if e[1].role != "system"]
        if window is not None and window >= 0:
            entries = entries[-window:]
        return [
            Message(role=spec.role, content=spec.text_of(a.data))
            for _, spec, a in entries
        ]

    def state(self, ctx: Context) -> dict[str, Any]:
        """`ChatAssistant(session_state=transcript.state)` history payload."""
        return {
            "messages": [
                {
                    "role": spec.role,
                    "text": spec.text_of(a.data),
                    "at": a.created_at.isoformat(),
                }
                for _, spec, a in self._entries(ctx)
            ]
        }


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

        `turn` defaults to the next index for a `user` message — derived from
        the max recorded turn, so pruning old messages doesn't restart the
        numbering — and to the current one for `assistant`/`system`.
        """
        if turn is None:
            turn = self._max_turn(ctx) + (1 if role == "user" else 0)
        return ctx.create(self._builder(role, text, turn, session_id), id=id)

    def create_turn(self, ctx: Context, text: str, session_id: str = "") -> str:
        """Creates the user message and returns its id — a `create_message=`
        hook for `ChatAssistant`/`run_message`."""
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
        if window is not None and window >= 0:
            artifacts = artifacts[-window:]
        return [Message(role=a.data.role, content=a.data.text) for a in artifacts]

    def state(self, ctx: Context) -> dict[str, Any]:
        """`ChatAssistant(session_state=conversation.state)` history payload."""
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
    """`ChatAssistant(session_state=conversation_state)` history payload."""
    return _DEFAULT.state(ctx)


__all__ = [
    "Conversation",
    "ConversationMessage",
    "MessageBuilder",
    "MessageSpec",
    "Role",
    "Transcript",
    "add_message",
    "conversation_state",
    "create_turn",
    "transcript",
    "turn_count",
]
