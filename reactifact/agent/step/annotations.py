"""Step annotations: Latest, Wakes, Scope and When."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic

from pydantic import BaseModel
from typing_extensions import TypeVar

from ...artifacts import Artifact

TOut = TypeVar("TOut", bound=BaseModel, default=BaseModel)


class Latest(Generic[TOut]):
    """Typed run() parameter marker: read the latest same-scope artifact of T.

    Unlike a plain `T` parameter, `Latest[T]` is resolved straight from the
    context (same `scope`) and does not need the type in `consumes` — it never
    triggers the step. It is the declarative replacement for
    `context.get(stage_id(...))` / `context.latest(T)` inside a step.
    """


@dataclass(frozen=True, slots=True)
class Wakes:
    """`Annotated[T, Wakes(False)]` — read T as input without waking the step."""

    value: bool = True


@dataclass(frozen=True, slots=True)
class Unscoped:
    """`Annotated[T, Unscoped()]` — read T across every turn, not just this scope.

    A step's `scope` (e.g. `TURN_SCOPE`) filters its typed artifact inputs to
    artifacts carrying the trigger's correlation value. History is the common
    exception: a follow-up step often needs *all* earlier answers, not only the
    current turn's. `Unscoped()` turns that filter off for one parameter:

        async def run(
            self,
            question: Question,
            answers: Annotated[list[Artifact[Answer]], Unscoped()],
        ) -> None: ...

    Use it with `list[T]` for data and `list[Artifact[T]]` for handles; combine
    with `Wakes(False)` when the history is a read, not a wake reason.
    """

    pass


@dataclass(frozen=True, slots=True)
class Scope:
    """Correlation scope for typed inputs.

    `Scope("query_id")` is the string shorthand. `Scope("question_id",
    owner=Question)` also matches `Question` by artifact id: the trigger's
    `question_id` IS the Question artifact id, so the owner itself has no
    duplicated field.
    """

    field: str
    owner: type[BaseModel] | None = None
    key: Callable[[BaseModel], Any] | None = None


@dataclass(frozen=True)
class When:
    """`Annotated[T, When(predicate)]` — a condition on an input artifact.

    `predicate` receives the artifact's `data` (the typed model), matching
    `Consume.condition`'s wrapper-level callable. In a `consumes` declaration or
    a typed `run()` parameter it becomes a `Consume(T, condition=...)`.
    """

    predicate: Callable[[Any], bool]

    def __call__(self, artifact: Artifact[Any]) -> bool:
        return self.predicate(artifact.data)
