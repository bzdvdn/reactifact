"""The `quick.agent` entry point: one LLM call → one typed artifact."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ..agents import create_agent
from ..budget import Budget
from ..consume import Consume
from ..context import Context
from ..produce import Produce, produce
from ..providers import LLMProvider
from ..structured import structured_llm
from ._shared import _arun, _default_resources, _question_of, _text_of
from .models import Question

TSchema = TypeVar("TSchema", bound=BaseModel)


def _structured_produce(
    schema: type[BaseModel],
    system: str,
    question_type: type[BaseModel],
    *,
    attempts: int,
    temperature: float | None,
    max_tokens: int | None,
) -> Produce[Any]:
    @produce(schema)
    async def _run(call: Any) -> None:
        question = _question_of(call, question_type)
        if question is None:
            return None
        body = await structured_llm(
            call.context,
            schema=schema,
            system=system,
            user=_text_of(question.data),
            attempts=attempts,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if body is None:
            return None
        call.effects.create_once_from(question, body)
        return None

    return _run


class QuickAgent(Generic[TSchema]):
    """One LLM call → one typed artifact, as a real reactive `Agent`.

    Built by `agent(...)`. `ask()` is the convenience path (fresh `Context`,
    create the `Question`, run, return the model); `.agent` and `.context` are
    the graduation path — pass `.agent` to your own `Runtime`, or keep using
    `.context` after an `ask()` to inspect the full artifact/provenance state.
    """

    def __init__(
        self,
        *,
        system: str,
        schema: type[TSchema],
        name: str = "quick",
        question_type: type[BaseModel] = Question,
        attempts: int = 2,
        temperature: float | None = None,
        max_tokens: int | None = None,
        llm: LLMProvider | None = None,
        budget: Budget | None = None,
        tracer: Any = None,
    ):
        self.schema = schema
        self.question_type = question_type
        self.resources = _default_resources(llm=llm)
        self.budget = budget
        self.tracer = tracer
        self.context: Context | None = None
        self.agent = create_agent(
            name=name,
            consumes=[Consume(question_type)],
            produces=[
                _structured_produce(
                    schema,
                    system,
                    question_type,
                    attempts=attempts,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            ],
        )

    async def ask(self, text: str, *, context: Context | None = None) -> TSchema | None:
        """Runs one question; returns the parsed model, or `None` on failure."""
        ctx = context if context is not None else Context(resources=self.resources)
        ctx.create(self.question_type(text=text))
        await _arun(ctx, [self.agent], budget=self.budget, tracer=self.tracer)
        self.context = ctx
        artifact = ctx.latest(self.schema)
        return artifact.data if artifact is not None else None


def agent(
    system: str,
    schema: type[TSchema],
    *,
    name: str = "quick",
    question_type: type[BaseModel] = Question,
    attempts: int = 2,
    temperature: float | None = None,
    max_tokens: int | None = None,
    llm: LLMProvider | None = None,
    budget: Budget | None = None,
    tracer: Any = None,
) -> QuickAgent[TSchema]:
    """A one-shot structured agent: `system` prompt + pydantic `schema`.

    Equivalent to the hand-written `structured_llm` pattern, but materialized as
    a reactive `Agent` (so it composes, traces, and re-runs on state like any
    other). Returns `None` from `.ask()` on the same honest-failure contract the
    rest of reactifact uses (no provider / provider error / unparseable reply).

    Pass `question_type=` to consume your own input model instead of the generic
    `Question` — it must have a `text: str` field (that's what `.ask(text)`
    fills and what the LLM receives).
    """
    return QuickAgent(
        system=system,
        schema=schema,
        name=name,
        question_type=question_type,
        attempts=attempts,
        temperature=temperature,
        max_tokens=max_tokens,
        llm=llm,
        budget=budget,
        tracer=tracer,
    )


__all__ = ["QuickAgent", "agent"]
