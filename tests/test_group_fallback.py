"""StepGroup fallback after retries, with atomic effects replacement."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    Effects,
    RetryPolicy,
    Runtime,
    Step,
    create_step_group,
)


class Question(BaseModel):
    text: str


class Partial(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class FailingStep(Step[Answer]):
    consumes = (Consume(Question),)
    produces = (Answer,)

    def __init__(self) -> None:
        self.attempts = 0
        super().__init__()

    async def run(self, question: Question, effects: Effects) -> None:
        self.attempts += 1
        effects.create(Partial(text="must be discarded"))
        raise RuntimeError(f"boom {self.attempts}")


class FallbackAnswer(Step[Answer]):
    produces = (Answer,)

    async def run(self, effects: Effects) -> None:
        effects.create(Answer(text="fallback"))


def _run(group, *, isolate_errors: bool = False) -> Context:
    context = Context()
    context.create(Question(text="go"))
    asyncio.run(Runtime(context, groups=[group], isolate_errors=isolate_errors).arun())
    return context


def test_fallback_runs_after_failure_and_discards_partial_effects():
    context = _run(
        create_step_group(
            "answer_builder",
            steps=[FailingStep()],
            fallback=FallbackAnswer(),
        )
    )
    assert [a.data.text for a in context.list_artifacts(Answer)] == ["fallback"]
    assert context.list_artifacts(Partial) == []


def test_fallback_runs_only_after_retries_are_exhausted():
    step = FailingStep()
    context = _run(
        create_step_group(
            "answer_builder",
            steps=[step],
            retry=RetryPolicy(max_attempts=2),
            fallback=FallbackAnswer(),
        )
    )
    assert step.attempts == 2
    assert [a.data.text for a in context.list_artifacts(Answer)] == ["fallback"]
    assert context.list_artifacts(Partial) == []


def test_fallback_does_not_swallow_authorization_errors():
    from reactifact.authz import AuthorizationError

    class Denied(Step[Answer]):
        consumes = (Consume(Question),)

        async def run(self) -> None:
            raise AuthorizationError("run", "answer")

    errors: list[BaseException] = []
    context = Context()
    context.create(Question(text="go"))
    group = create_step_group(
        "denied",
        steps=[Denied()],
        fallback=FallbackAnswer(),
        fallback_on=(Exception,),
    )
    asyncio.run(
        Runtime(
            context,
            groups=[group],
            isolate_errors=True,
            on_agent_error=lambda _g, _e, exc: errors.append(exc),
        ).arun()
    )
    assert not context.list_artifacts(Answer)
    assert errors and isinstance(errors[0], AuthorizationError)


def test_fallback_failure_propagates_without_isolation():
    class BrokenFallback(Step[Answer]):
        produces = (Answer,)

        async def run(self) -> None:
            raise RuntimeError("fallback broke")

    with pytest.raises(RuntimeError, match="fallback broke"):
        _run(
            create_step_group(
                "answer_builder",
                steps=[FailingStep()],
                fallback=BrokenFallback(),
            )
        )
