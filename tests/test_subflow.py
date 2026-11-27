"""Subflows: isolated child context, provenance links, trace and budget."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Artifact,
    Budget,
    Consume,
    Context,
    Runtime,
    RuntimeResources,
    Step,
    run_subflow,
)
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse, Message
from reactifact.tracing import Tracer


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Final(BaseModel):
    text: str
    child_run_id: str
    parent_run_id: str
    child_outputs: int
    relations: int


class ChildStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, question: Question) -> Answer:
        return Answer(text=question.text.upper())


class ChildAgent(Agent):
    consumes = [Consume(Question)]

    def __init__(self) -> None:
        super().__init__(name="child", steps=[ChildStep()])


class ParentStep(Step):
    consumes = (Question,)
    produces = (Final,)

    async def run(self, context: Context, trigger: Artifact[Question]) -> Final:
        result = await run_subflow(
            context,
            groups=[ChildAgent()],
            input=trigger,
            output_type=Answer,
            link="supported_by",
            source=trigger,
        )
        assert result.output is not None
        return Final(
            text=result.output.data.text,
            child_run_id=result.run_id,
            parent_run_id=result.parent_run_id,
            child_outputs=len(result.outputs),
            relations=len(result.context.relations()),
        )


def test_subflow_returns_child_output_and_links_provenance():
    context = Context()
    question = context.create(Question(text="hello"), id="question:1")
    runtime = Runtime(
        context,
        groups=[Agent("parent", steps=[ParentStep()])],
        tracer=Tracer(),
    )

    asyncio.run(runtime.arun())

    final = context.latest(Final)
    assert final is not None
    assert final.data.text == "HELLO"
    assert final.data.child_outputs == 1
    assert final.data.relations == 1  # answer supported_by question
    assert final.data.child_run_id
    assert final.data.parent_run_id == runtime.run_id
    assert context.get(question.id) is not None  # parent state intact


class CountingLLM(LLMProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text="child answer",
            usage={"prompt_tokens": 7, "completion_tokens": 3},
        )

    async def stream(self, request):
        yield LLMResponse(text="")


class LLMChildStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, context: Context, question: Question) -> Answer:
        llm = context.resources.llm
        assert llm is not None
        response = await llm.complete(
            LLMRequest(messages=[Message.user(question.text)])
        )
        return Answer(text=response.text)


class LLMParentStep(Step):
    consumes = (Question,)
    produces = (Final,)

    async def run(self, context: Context, trigger: Artifact[Question]) -> Final:
        result = await run_subflow(
            context,
            groups=[Agent("child", steps=[LLMChildStep()])],
            input=trigger,
            output_type=Answer,
        )
        assert result.output is not None
        return Final(
            text=result.output.data.text,
            child_run_id=result.run_id,
            parent_run_id=result.parent_run_id,
            child_outputs=len(result.outputs),
            relations=len(result.context.relations()),
        )


def test_subflow_adds_child_usage_to_parent_budget():
    resources = RuntimeResources(llm=CountingLLM())
    context = Context(resources=resources)
    context.create(Question(text="hello"), id="question:1")
    runtime = Runtime(
        context,
        groups=[Agent("parent", steps=[LLMParentStep()])],
        budget=Budget(max_tokens=100),
        tracer=Tracer(),
    )

    asyncio.run(runtime.arun())

    assert runtime.last_stats is not None
    assert runtime.last_stats.prompt_tokens == 7
    assert runtime.last_stats.completion_tokens == 3


class Note(BaseModel):
    text: str


class ReadNoteStep(Step):
    consumes = (Question,)
    produces = (Answer,)

    async def run(self, context: Context, question: Question) -> Answer:
        note = context.latest(Note)
        assert note is not None
        return Answer(text=note.data.text)


class BranchParentStep(Step):
    consumes = (Question,)
    produces = (Final,)

    async def run(self, context: Context, trigger: Artifact[Question]) -> Final:
        result = await run_subflow(
            context,
            groups=[Agent("child", steps=[ReadNoteStep()])],
            input=trigger,
            output_type=Answer,
            isolate=False,
        )
        assert result.output is not None
        return Final(
            text=result.output.data.text,
            child_run_id=result.run_id,
            parent_run_id=result.parent_run_id,
            child_outputs=len(result.outputs),
            relations=len(result.context.relations()),
        )


def test_subflow_with_isolate_false_sees_parent_state():
    context = Context()
    context.create(Note(text="parent note"), id="note:1")
    context.create(Question(text="go"), id="question:1")
    runtime = Runtime(
        context,
        groups=[Agent("parent", steps=[BranchParentStep()])],
        tracer=Tracer(),
    )

    asyncio.run(runtime.arun())

    final = context.latest(Final)
    assert final is not None
    assert final.data.text == "parent note"
    assert final.data.child_outputs == 1
