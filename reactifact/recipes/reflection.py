"""recipes.reflection — generate → critique → regenerate (§24, §42, §69).

Ported from `examples/reflection`, generalized the same way as
`recipes.plan_execute`/`recipes.supervisor`: the recipe owns the control
flow (round-capping, the accept threshold, idempotent re-entry, completion
detection), the domain owns the three decisions that actually need
judgement — `draft`, `critique`, `rewrite` — plus `finish`.

Unlike `PlanExecute` (immutable, indexed step artifacts), the working state
here is *one mutable draft artifact per topic*, updated in place each round
— matching the ported example's own model (`effects.update(draft, ...)`,
not a new artifact per round); `Review` artifacts still accumulate one per
round, as an audit trail the loop itself never re-reads past the latest one.

    class MyLoop(ReflectionLoop[Topic, Draft, Review, Final]):
        topic_type, draft_type, review_type, final_type = Topic, Draft, Review, Final
        accept_at = 0.8
        max_rounds = 2

        async def draft(self, context, topic) -> Draft: ...
        async def critique(self, context, draft) -> tuple[float, str]: ...
        async def rewrite(self, context, draft, feedback) -> Draft: ...
        async def finish(self, context, draft) -> Final: ...

    class Flow(Agent):
        consumes = [Consume(Topic), Consume(Draft), Consume(Review)]
        produces = MyLoop().produces()

`draft_type` needs `topic_field`/`round_field`/`status_field` with defaults
(e.g. `topic: str = ""`, `round: int = 0`, `status: str = "draft"`) —
`draft()`/`rewrite()` don't need to fill them in, the recipe stamps the
real values on every create/update. `review_type` needs `topic_field` too
(a `Review` is only ever reached by reacting to *its own* creation, so the
recipe must be able to read the topic straight off it — no draft/relation
lookup happens first) plus `review_score_field`/`review_feedback_field`
(e.g. `topic: str = ""`, `score: float = 0.0`, `feedback: str = ""`) —
`critique()` returns a plain `(score, feedback)` tuple, the recipe builds
the `Review` artifact from it and stamps `topic_field`/`round_field`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ..artifacts import Artifact
from ..context import Context
from ..events import Event
from ..produce import Produce

TopicT = TypeVar("TopicT", bound=BaseModel)
DraftT = TypeVar("DraftT", bound=BaseModel)
ReviewT = TypeVar("ReviewT", bound=BaseModel)
FinalT = TypeVar("FinalT", bound=BaseModel)


class ReflectionLoop(Generic[TopicT, DraftT, ReviewT, FinalT], ABC):
    """Subclass with the four domain hooks; `produces()` gives the four
    `Produce`s to wire into an `Agent` (see module docstring)."""

    topic_type: type[TopicT]
    draft_type: type[DraftT]
    review_type: type[ReviewT]
    final_type: type[FinalT]

    accept_at: float = 0.8
    max_rounds: int = 2

    topic_field: str = "topic"
    round_field: str = "round"
    status_field: str = "status"
    draft_status: str = "draft"
    accepted_status: str = "accepted"
    review_score_field: str = "score"
    review_feedback_field: str = "feedback"

    @abstractmethod
    async def draft(self, context: Context, topic: Artifact[TopicT]) -> DraftT:
        """The first draft for `topic` (round 0)."""

    @abstractmethod
    async def critique(
        self, context: Context, draft: Artifact[DraftT]
    ) -> tuple[float, str]:
        """`(score 0..1, feedback)` — accepted once `score >= accept_at`."""

    @abstractmethod
    async def rewrite(
        self, context: Context, draft: Artifact[DraftT], feedback: str
    ) -> DraftT:
        """The improved draft, given the critic's feedback. Every field of
        the returned instance replaces the stored draft's (`round`/`status`/
        `topic_field` are then overwritten by the recipe regardless)."""

    @abstractmethod
    async def finish(self, context: Context, draft: Artifact[DraftT]) -> FinalT:
        """Builds the final artifact once `draft` is accepted or out of rounds."""

    def produces(self) -> list[Produce[Any]]:
        return [_Draft(self), _Critic(self), _Rewrite(self), _Finalize(self)]

    def _draft_id(self, topic_id: str) -> str:
        return f"draft:{topic_id}"

    def _review_id(self, draft_id: str, round_: int) -> str:
        return f"review:{draft_id}:{round_}"

    def _final_id(self, topic_id: str) -> str:
        return f"final:{topic_id}"

    def _topic_id_of(self, artifact: Artifact[Any]) -> str | None:
        if isinstance(artifact.data, self.topic_type):
            return artifact.id
        value = getattr(artifact.data, self.topic_field, None)
        return value if isinstance(value, str) else None


class _Draft(Produce[Any]):
    def __init__(self, owner: ReflectionLoop[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.draft_type)

    async def produce(
        self, context: Context, inputs: list[Artifact[Any]], event: Event | None = None
    ) -> None:
        owner = self.owner
        artifact = context.get(event.artifact_id) if event is not None else None
        if artifact is None:
            return None
        topic_id = owner._topic_id_of(artifact)
        if topic_id is None:
            return None
        draft_id = owner._draft_id(topic_id)
        if context.get(draft_id) is not None:
            return None  # already drafted (§42)
        topic = context.get(topic_id)
        if topic is None:
            return None
        draft_data = await owner.draft(context, topic)
        stamped = draft_data.model_copy(
            update={
                owner.topic_field: topic_id,
                owner.round_field: 0,
                owner.status_field: owner.draft_status,
            }
        )
        handle = self.effects.create(stamped, id=draft_id)
        handle.link("from_topic", topic_id)
        return None


class _Critic(Produce[Any]):
    def __init__(self, owner: ReflectionLoop[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.review_type)

    async def produce(
        self, context: Context, inputs: list[Artifact[Any]], event: Event | None = None
    ) -> None:
        owner = self.owner
        artifact = context.get(event.artifact_id) if event is not None else None
        if artifact is None:
            return None
        topic_id = owner._topic_id_of(artifact)
        if topic_id is None:
            return None
        draft = context.get(owner._draft_id(topic_id))
        if draft is None or getattr(draft.data, owner.status_field) != owner.draft_status:
            return None
        round_ = getattr(draft.data, owner.round_field)
        review_id = owner._review_id(draft.id, round_)
        if context.get(review_id) is not None:
            return None  # already reviewed this round (§42)
        score, feedback = await owner.critique(context, draft)
        review_data = owner.review_type(
            **{
                owner.review_score_field: score,
                owner.review_feedback_field: feedback,
            }
        )
        stamped = review_data.model_copy(
            update={owner.round_field: round_, owner.topic_field: topic_id}
        )
        handle = self.effects.create(stamped, id=review_id)
        handle.link("reviews", draft)
        if score >= owner.accept_at:
            self.effects.update(draft, **{owner.status_field: owner.accepted_status})
        return None


class _Rewrite(Produce[Any]):
    def __init__(self, owner: ReflectionLoop[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.draft_type)

    async def produce(
        self, context: Context, inputs: list[Artifact[Any]], event: Event | None = None
    ) -> None:
        owner = self.owner
        artifact = context.get(event.artifact_id) if event is not None else None
        if artifact is None:
            return None
        topic_id = owner._topic_id_of(artifact)
        if topic_id is None:
            return None
        draft = context.get(owner._draft_id(topic_id))
        if draft is None:
            return None
        status = getattr(draft.data, owner.status_field)
        round_ = getattr(draft.data, owner.round_field)
        if status == owner.accepted_status or round_ + 1 > owner.max_rounds:
            return None
        review = context.get(owner._review_id(draft.id, round_))
        if review is None:
            return None  # wait for this round's critique
        feedback = getattr(review.data, owner.review_feedback_field)
        improved = await owner.rewrite(context, draft, feedback)
        update_fields = improved.model_dump()
        update_fields[owner.round_field] = round_ + 1
        update_fields[owner.status_field] = owner.draft_status
        update_fields[owner.topic_field] = topic_id
        self.effects.update(draft, **update_fields)
        return None


class _Finalize(Produce[Any]):
    def __init__(self, owner: ReflectionLoop[Any, Any, Any, Any]):
        self.owner = owner
        super().__init__(artifact_type=owner.final_type)

    async def produce(
        self, context: Context, inputs: list[Artifact[Any]], event: Event | None = None
    ) -> None:
        owner = self.owner
        artifact = context.get(event.artifact_id) if event is not None else None
        if artifact is None:
            return None
        topic_id = owner._topic_id_of(artifact)
        if topic_id is None:
            return None
        draft = context.get(owner._draft_id(topic_id))
        if draft is None:
            return None
        status = getattr(draft.data, owner.status_field)
        round_ = getattr(draft.data, owner.round_field)
        done = status == owner.accepted_status or round_ >= owner.max_rounds
        if not done:
            return None
        final_id = owner._final_id(topic_id)
        if context.get(final_id) is not None:
            return None  # already finalized
        final_data = await owner.finish(context, draft)
        if hasattr(final_data, owner.topic_field):
            final_data = final_data.model_copy(update={owner.topic_field: topic_id})
        handle = self.effects.create(final_data, id=final_id)
        handle.link("based_on", draft)
        return None
