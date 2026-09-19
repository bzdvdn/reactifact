"""Internal helpers shared by the `quick` submodules (not public API)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from pydantic import BaseModel

from ..agents import Agent
from ..artifacts import Artifact
from ..budget import Budget
from ..context import Context
from ..providers import LLMProvider, from_env
from ..recipes import find
from ..resources import RuntimeResources
from ..runtime import Runtime
from ..sources import Source
from .models import Question


async def _arun(
    context: Context,
    agents: Sequence[Agent],
    *,
    budget: Budget | None,
    tracer: Any,
) -> None:
    await Runtime(context, agents=list(agents), budget=budget, tracer=tracer).arun()


def _default_resources(
    *,
    llm: LLMProvider | None,
    sources: dict[str, Source] | None = None,
) -> RuntimeResources:
    """`llm=None` means "the environment default" (`from_env()`), not "no model".

    `from_env()` returns `None` when no key is configured — which is the honest
    offline path every reactifact helper already handles (a `None` reply, not a
    crash), so a demo still runs without a key.
    """
    return RuntimeResources(
        llm=llm if llm is not None else from_env(),
        sources=sources,
    )


def _question_of(
    call: Any, question_type: type[BaseModel] = Question
) -> Artifact[Any] | None:
    """The triggering question if this run was woken by one, else the first input.

    `question_type` defaults to the generic `Question`; pass your own model to
    use a domain-shaped input. Any custom type must expose a `text: str` field
    (that's what the facade sends as the LLM query/prompt).

    Preferring `call.trigger` keeps a reused `Context` correct: after a second
    question exists, `find(inputs, Question)` would still return the first one.
    """
    trigger = cast("Artifact[Any] | None", call.trigger)
    if trigger is not None and isinstance(trigger.data, question_type):
        return trigger
    return find(call.inputs, question_type)


def _text_of(value: BaseModel) -> str:
    """The `text` field of a model, or "" — the facade's query/prompt contract."""
    return str(getattr(value, "text", "") or "")


__all__: list[str] = []
