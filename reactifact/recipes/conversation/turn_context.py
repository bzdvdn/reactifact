"""One ready-to-consume conversation context per user turn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from ...artifacts import Artifact
from ...consume import Consume
from ...context import Context
from ...step import Emit, Ensure, Step
from ._common import _windowed
from .models import ConversationMessage
from .views import MessageSpec, Transcript


class TurnContext(BaseModel):
    """One turn's ready-to-consume conversation context.

    Built by `TurnContextStep`: the current query, the previous user query, the
    bounded window and the rolling digest, so downstream steps do not need a
    `Context` just to reconstruct the conversation. `history` uses
    `ConversationMessage` by default; pass a custom `build=` to
    `TurnContextStep` for a domain shape.
    """

    turn_id: str
    query: str
    resolved_query: str = ""
    previous_query: str | None = None
    is_follow_up: bool = False
    history: list[ConversationMessage] = Field(default_factory=list)
    digest: str = ""


def looks_like_follow_up(text: str, *, max_words: int = 4) -> bool:
    """A cheap, deterministic follow-up heuristic (no LLM).

    Short messages, pronouns and common continuation words are treated as
    follow-ups. It is deliberately conservative; pass your own callback to
    `TurnContextStep` when the domain has better signals.
    """
    words = text.strip().lower().split()
    if not words:
        return False
    if len(words) <= max_words:
        return True
    starts = {
        "and",
        "but",
        "so",
        "then",
        "why",
        "how",
        "what",
        "which",
        "who",
        "where",
        "when",
        "а",
        "но",
        "и",
        "тогда",
        "почему",
        "как",
        "что",
        "какой",
        "какая",
        "где",
        "когда",
    }
    return words[0].strip(",.!?;:") in starts


@dataclass(frozen=True)
class TurnFacts:
    """Everything `TurnContextStep.build` can use for a domain-specific artifact.

    The recipe owns the standard window/digest/previous-turn math; a domain
    `build` callback maps these facts to its own artifact (e.g. a
    `DialogueContext` with `prior_domain`/`routing_text`) without reading
    `Context` again.
    """

    trigger: Artifact[Any]
    query: str
    resolved_query: str
    previous_query: str | None
    previous_artifact: Artifact[Any] | None
    previous_answer: Artifact[Any] | None
    history: list[ConversationMessage]
    digest: str
    is_follow_up: bool


class TurnContextStep(Step[TurnContext]):
    """Builds one `TurnContext` artifact per user turn.

    The step consumes the user-message type, renders the conversation with
    `Transcript`, reads the rolling digest artifact by id and emits a stable
    `turn:{trigger.id}` artifact once per turn. Downstream steps can consume
    `TurnContext` instead of carrying `Context` or re-deriving the window.

    `is_follow_up`/`resolve` are callbacks; the default is deterministic
    (`looks_like_follow_up`) and never calls an LLM. `build` receives
    `TurnFacts` and returns the artifact payload (default: `TurnContext`), so a
    domain context can add prior-domain/routing fields without carrying
    `Context`. `consume=` defaults to `Consume(user_type)`; pass
    `OnCreate(user_type)` for one artifact per turn, and `ensure=True` to use
    `Ensure` instead of `Emit` (idempotent create).
    """

    def __init__(
        self,
        user_type: type[BaseModel],
        *,
        assistant_type: type[BaseModel] | None = None,
        specs: list[MessageSpec] | None = None,
        digest_type: type[BaseModel] | None = None,
        artifact_type: type[BaseModel] = TurnContext,
        digest_id: str = "digest",
        window: int | None = 12,
        render: Callable[[Any], str] | None = None,
        follow_up: Callable[[str, str | None], bool] | None = None,
        resolve: Callable[[str, str | None, bool], str] | None = None,
        build: Callable[[TurnFacts], BaseModel] | None = None,
        consume: Consume | None = None,
        ensure: bool = False,
        name: str = "turn_context",
    ) -> None:
        if window is not None and window < 0:
            raise ValueError("window must be >= 0 or None")
        if not digest_id:
            raise ValueError("digest_id must be a non-empty string")
        super().__init__(
            artifact_type,
            consumes=consume or Consume(user_type),
            name=name,
        )
        self.ensure = ensure
        self.user_type = user_type
        self.assistant_type = assistant_type
        self.specs = list(specs) if specs is not None else None
        self.digest_type = digest_type
        self.digest_id = digest_id
        self.window = window
        self.render = render or (lambda data: getattr(data, "text", str(data)))
        self._follow_up = follow_up or (lambda text, _prev: looks_like_follow_up(text))
        self._resolve = resolve
        self._build = build

    def _message_specs(self) -> list[MessageSpec]:
        if self.specs is not None:
            return self.specs
        specs = [MessageSpec(self.user_type, "user")]
        if self.assistant_type is not None:
            specs.append(MessageSpec(self.assistant_type, "assistant"))
        return specs

    async def run(
        self, context: Context, trigger: Artifact[Any] | None
    ) -> Emit | Ensure | None:
        if trigger is None or not isinstance(trigger.data, self.user_type):
            return None
        query = self.render(trigger.data)
        view = Transcript(self._message_specs())
        entries = view.entries(context)
        current_index = next(
            (
                index
                for index, entry in enumerate(entries)
                if entry.artifact.id == trigger.id
            ),
            None,
        )
        if current_index is None:
            return None
        prior = entries[:current_index]
        previous_artifact = next(
            (
                entry.artifact
                for entry in reversed(prior)
                if entry.spec.type is self.user_type
            ),
            None,
        )
        previous_answer = next(
            (
                entry.artifact
                for entry in reversed(prior)
                if entry.spec.role == "assistant"
            ),
            None,
        )
        window_entries = _windowed(entries[: current_index + 1], self.window)
        history = [
            ConversationMessage(
                role=entry.spec.role,
                text=entry.spec.text_of(entry.artifact.data),
                turn=getattr(entry.artifact.data, "turn", 0),
                session_id=getattr(entry.artifact.data, "session_id", ""),
            )
            for entry in window_entries
        ]
        previous: str | None = (
            self.render(previous_artifact.data)
            if previous_artifact is not None
            else None
        )
        digest = ""
        digest_artifact = context.get(self.digest_id)
        if digest_artifact is not None and (
            self.digest_type is None
            or isinstance(digest_artifact.data, self.digest_type)
        ):
            digest = str(getattr(digest_artifact.data, "text", ""))
        is_follow_up = bool(self._follow_up(query, previous))
        resolved = (
            self._resolve(query, previous, is_follow_up)
            if self._resolve is not None
            else query
        )
        facts = TurnFacts(
            trigger=trigger,
            query=query,
            resolved_query=resolved,
            previous_query=previous,
            previous_artifact=previous_artifact,
            previous_answer=previous_answer,
            history=history,
            digest=digest,
            is_follow_up=is_follow_up,
        )
        payload = (
            self._build(facts)
            if self._build is not None
            else TurnContext(
                turn_id=trigger.id,
                query=query,
                resolved_query=resolved,
                previous_query=previous,
                is_follow_up=is_follow_up,
                history=history,
                digest=digest,
            )
        )
        if self.ensure:
            return Ensure(payload, id=f"turn:{trigger.id}")
        return Emit(payload, id=f"turn:{trigger.id}")
