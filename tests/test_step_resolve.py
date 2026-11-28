from __future__ import annotations

import asyncio
from typing import Annotated, Any

import pytest
from pydantic import BaseModel
from reactifact import (
    Artifact,
    Context,
    Latest,
    Runtime,
    Step,
    StepInvocation,
    Wakes,
    step,
)
from reactifact.agent.step.resolve import MissingInputError


class Question(BaseModel):
    text: str


class Doc(BaseModel):
    text: str


class Note(BaseModel):
    text: str


class Out(BaseModel):
    text: str


class ProgressLike(BaseModel):
    text: str


class TypedProgressStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(self, question: Question, progress: ProgressLike) -> Out:
        # A typed parameter named `progress` must not receive the collaborator.
        assert isinstance(progress, ProgressLike)
        return Out(text=progress.text)


def test_typed_name_does_not_get_a_collaborator() -> None:
    context = Context()
    context.create(Question(text="q"))
    context.create(ProgressLike(text="from-artifact"))

    asyncio.run(Runtime(context, steps=[TypedProgressStep()]).arun())

    assert context.latest(Out).data.text == "from-artifact"


class AnnotatedHandleStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(
        self,
        question: Question,
        doc: Annotated[Artifact[Doc], Wakes(False)],
    ) -> Out:
        assert isinstance(doc, Artifact)
        return Out(text=doc.data.text)


def test_annotated_artifact_returns_a_handle() -> None:
    context = Context()
    context.create(Question(text="q"))
    context.create(Doc(text="doc"))

    asyncio.run(Runtime(context, steps=[AnnotatedHandleStep()]).arun())

    assert context.latest(Out).data.text == "doc"


class AnnotatedLatestStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(
        self,
        question: Question,
        latest: Annotated[Latest[Doc], Wakes(False)],
    ) -> Out:
        return Out(text=latest.text if latest else "none")


def test_annotated_latest_is_still_latest() -> None:
    context = Context()
    context.create(Question(text="q"))
    context.create(Doc(text="doc"))

    asyncio.run(Runtime(context, steps=[AnnotatedLatestStep()]).arun())

    assert context.latest(Out).data.text == "doc"


class LatestUnionStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(
        self,
        question: Question,
        latest: Latest[Doc | Note],
    ) -> Out:
        return Out(text=f"{type(latest).__name__}:{latest.text}" if latest else "none")


def test_latest_considers_every_union_member() -> None:
    context = Context()
    context.create(Question(text="q"))
    context.create(Doc(text="doc"))
    context.create(Note(text="note"))

    asyncio.run(Runtime(context, steps=[LatestUnionStep()]).arun())

    assert context.latest(Out).data.text == "Note:note"


class LatestHandleStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(
        self,
        question: Question,
        latest: Latest[Artifact[Doc]],
    ) -> Out:
        assert isinstance(latest, Artifact)
        return Out(text=latest.data.text)


def test_latest_of_an_artifact_returns_a_handle() -> None:
    context = Context()
    context.create(Question(text="q"))
    context.create(Doc(text="doc"))

    asyncio.run(Runtime(context, steps=[LatestHandleStep()]).arun())

    assert context.latest(Out).data.text == "doc"


class LatestMissingStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(self, question: Question, latest: Latest[Doc]) -> Out:
        return Out(text=latest.text if latest else "none")


def test_latest_without_a_value_is_none() -> None:
    context = Context()
    context.create(Question(text="q"))

    asyncio.run(Runtime(context, steps=[LatestMissingStep()]).arun())

    assert context.latest(Out).data.text == "none"


class OptionalContextStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(self, question: Question, context: Context | None) -> Out:
        # Optional collaborators are still injected (not resolved as data).
        assert context is not None
        return Out(text="ok")


def test_optional_collaborator_is_injected() -> None:
    context = Context()
    context.create(Question(text="q"))

    asyncio.run(Runtime(context, steps=[OptionalContextStep()]).arun())

    assert context.latest(Out).data.text == "ok"


class NeedsAnyHandleStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(self, question: Question, doc: Artifact[Any]) -> Out:
        return Out(text="unreachable")


def test_missing_any_handle_message_is_readable() -> None:
    context = Context()
    context.create(Question(text="q"))

    with pytest.raises(MissingInputError, match="expects any artifact"):
        asyncio.run(Runtime(context, steps=[NeedsAnyHandleStep()]).arun())


class NeedsDocStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(self, question: Question, doc: Doc) -> Out:
        return Out(text=doc.text)


def test_missing_input_lists_available_types_and_producers() -> None:
    context = Context()
    context.create(Question(text="q"))

    with pytest.raises(MissingInputError) as caught:
        asyncio.run(Runtime(context, steps=[NeedsDocStep()]).arun())

    message = str(caught.value)
    assert "Available types:" in message
    assert "Question" in message
    assert "No step in this runtime produces it" in message


class OptionalAnnotatedContextStep(Step):
    reads = (Question,)
    produces = (Out,)

    async def run(
        self,
        question: Question,
        context: Annotated[Context, "meta"] | None,
    ) -> Out:
        assert context is not None
        return Out(text="ok")


def test_optional_annotated_collaborator_is_injected() -> None:
    context = Context()
    context.create(Question(text="q"))

    asyncio.run(Runtime(context, steps=[OptionalAnnotatedContextStep()]).arun())

    assert context.latest(Out).data.text == "ok"


def test_invocation_parameter_name_is_free_when_annotated() -> None:
    # This module uses `from __future__ import annotations`, so the annotation
    # reaches the resolver as a string: the documented `inv: StepInvocation`
    # spelling must resolve through the hints, not the parameter name.
    @step(reads=Question, produces=Out)
    async def answer(inv: StepInvocation) -> None:
        inv.effects.create(Out(text=inv.trigger.data.text.upper()))

    context = Context()
    context.create(Question(text="q"))

    asyncio.run(Runtime(context, steps=[answer]).arun())

    assert context.latest(Out).data.text == "Q"
