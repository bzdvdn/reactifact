import asyncio
from typing import Annotated

from pydantic import BaseModel
from reactifact import Agent, Context, Effects, Runtime, Wakes, When, step
from reactifact.step import Step


class Ticket(BaseModel):
    status: str
    text: str


class Out(BaseModel):
    text: str


class PickOpen(Step):
    produces = (Out,)

    async def run(
        self,
        ticket: Annotated[Ticket, When(lambda t: t.status == "open")],
        effects: Effects,
    ) -> None:
        effects.upsert(Out(text=ticket.text), id=f"out:{ticket.text}")


class PickAgent(Agent):
    steps = [PickOpen()]


def test_annotated_when_derives_consumes_and_filters_inputs():
    context = Context()
    context.create(Ticket(status="open", text="open-1"))
    context.create(Ticket(status="closed", text="closed-1"))
    context.create(Ticket(status="open", text="open-2"))

    asyncio.run(Runtime(context, agents=[PickAgent()]).arun())

    assert {o.data.text for o in context.list_artifacts(Out)} == {"open-1", "open-2"}


class DeclaredConsume(Step):
    consumes = (Annotated[Ticket, When(lambda t: t.status == "open")],)
    produces = (Out,)

    async def run(self, ticket: Ticket, effects: Effects) -> None:
        effects.upsert(Out(text=ticket.text), id=f"declared:{ticket.text}")


class DeclaredAgent(Agent):
    steps = [DeclaredConsume()]


def test_annotated_when_works_in_explicit_consumes():
    context = Context()
    context.create(Ticket(status="open", text="yes"))
    context.create(Ticket(status="closed", text="no"))

    asyncio.run(Runtime(context, agents=[DeclaredAgent()]).arun())

    assert {o.data.text for o in context.list_artifacts(Out)} == {"yes"}


class History(BaseModel):
    text: str


class WithHistory(Step):
    produces = (Out,)

    async def run(
        self,
        ticket: Ticket,
        history: Annotated[History, Wakes(False)],
        effects: Effects,
    ) -> None:
        effects.upsert(Out(text=f"{ticket.text}:{history.text}"), id="with-history")


def test_wakes_false_annotation_reads_without_waking():
    step = WithHistory()
    specs = {spec.artifact_type: spec.wakes for spec in step.consume_specs() or ()}
    assert specs[Ticket] is True
    assert specs[History] is False

    context = Context()
    context.create(Ticket(status="open", text="q"))
    context.create(History(text="h"))

    asyncio.run(
        Runtime(context, agents=[Agent(name="with-history", steps=[step])]).arun()
    )
    assert context.latest(Out).data.text == "q:h"


@step(produces=Out)
async def typed_function_step(ticket: Ticket, effects: Effects) -> None:
    effects.upsert(Out(text=f"typed:{ticket.text}"), id="typed-function")


def test_step_decorator_accepts_typed_parameters():
    step = typed_function_step
    specs = {spec.artifact_type: spec.wakes for spec in step.consume_specs() or ()}
    assert specs[Ticket] is True

    context = Context()
    context.create(Ticket(status="open", text="hello"))
    asyncio.run(Runtime(context, agents=[Agent(name="typed", steps=[step])]).arun())
    assert context.latest(Out).data.text == "typed:hello"
