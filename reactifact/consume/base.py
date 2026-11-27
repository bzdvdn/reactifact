"""The declarative consume contract and its simple factories."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..artifacts import Artifact, ArtifactType
from ..events import EventType
from ..selector import Selector
from ..triggers import Trigger


class Consume(Selector):
    """Describes the consumed artifact type, condition and triggering events.

    All parameters can be set as class attributes (for inheritance)
    or passed to the constructor.

    `wakes` (default `True`): whether this `Consume` also contributes to the
    agent's `triggers` (`Agent.matches()` — does the agent run at all).
    `False` means "read this as input, but don't wake up on it" — e.g. an
    agent that should run when a `Question` arrives but also wants the
    existing `ConversationHistory` as input, without re-running once per
    history artifact. For the *declarative* style (`consumes`/`produces`,
    the common case), this covers the one real reason to decouple "what
    wakes me" from "what I read" without reaching for `Agent`'s separate
    `triggers=` override. `triggers=` itself still exists and is not
    deprecated — it's the only option for the *imperative* style (an `Agent`
    subclass that overrides `run()` directly and has no `consumes` at all to
    attach a condition to; see `Agent.triggers`'s own docstring).

    `include_subclasses` (default `False`): wake on artifacts whose type is a
    subclass of `artifact_type`, not only the exact type — the declarative
    form of a marker base / protocol base (`Consume(ChatMessage,
    include_subclasses=True)` fires for `UserQuery` and `Answer`). `collect()`
    already reads by `issubclass` (`Context.list_artifacts`), so the flag only
    affects triggering. Opt-in so an agent subscribed to both a base and a
    concrete type never wakes twice for the same event.

    `debounce` (default `False`): when several events in the same
    generation would each independently wake this agent via this `Consume`
    (a fan-out step creating five `Evidence` artifacts in one commit, five
    separate `ARTIFACT_CREATED` events), collapse them into a single run
    instead of five — the fifth (last) event is what the runtime happens to
    pass as `event`, but a debounced produce should read `inputs`
    (collected fresh from `Context` regardless of which event triggered the
    run) rather than rely on `event` for "what changed", since that's
    exactly the information debouncing discards. See
    `Runtime._arun_once_impl` for where the collapsing actually happens —
    `Consume`/`Trigger` only carry the flag, they don't implement it, since
    collapsing needs to compare *other* events in the same drained batch,
    which is Runtime-level state neither of them has access to.
    """

    wakes: bool = True
    debounce: bool = False

    def __init__(
        self,
        artifact_type: ArtifactType | None = None,
        condition: Callable[[Artifact[Any]], bool] | None = None,
        event_types: Sequence[EventType] | None = None,
        *,
        wakes: bool | None = None,
        debounce: bool | None = None,
        include_subclasses: bool | None = None,
    ):
        super().__init__(
            artifact_type,
            condition,
            event_types,
            include_subclasses=include_subclasses,
        )
        self.wakes = wakes if wakes is not None else self.__class__.wakes
        self.debounce = debounce if debounce is not None else self.__class__.debounce
        if self.debounce and not self.wakes:
            raise ValueError(
                "Consume(debounce=True, wakes=False) is meaningless: debounce "
                "only collapses repeat wake-ups, and wakes=False means this "
                "Consume never wakes the agent in the first place"
            )

    @classmethod
    def on_delete(cls, artifact_type: ArtifactType, **kwargs: Any) -> Consume:
        """Wake when an artifact is deleted."""
        return cls(
            artifact_type,
            event_types=(EventType.ARTIFACT_DELETED,),
            **kwargs,
        )

    def to_triggers(self) -> list[Trigger]:
        """Converts into a list of triggers for automatic reaction.

        Empty when `wakes=False` — this `Consume` still feeds
        `Agent._collect_inputs()`, it just never causes `Agent.matches()`
        to fire on its own.
        """
        if not self.wakes:
            return []
        return [
            Trigger(
                event_type,
                self.artifact_type,
                self.condition,
                debounce=self.debounce,
                include_subclasses=self.include_subclasses,
            )
            for event_type in self.event_types
        ]

    @classmethod
    def by_base(
        cls,
        artifact_type: ArtifactType,
        event_types: Sequence[EventType] = (
            EventType.ARTIFACT_CREATED,
            EventType.ARTIFACT_UPDATED,
        ),
        *,
        wakes: bool = True,
        debounce: bool = False,
    ) -> Consume:
        """Creates a Consume that also wakes on subclasses of `artifact_type`.

        Sugar for `Consume(artifact_type, include_subclasses=True)` — e.g. a
        marker base `ChatMessage` with concrete `UserQuery`/`Answer` artifacts.
        """
        return cls(
            artifact_type,
            event_types=event_types,
            wakes=wakes,
            debounce=debounce,
            include_subclasses=True,
        )


__all__ = ["Consume"]
