import asyncio
from typing import Annotated

from pydantic import BaseModel
from reactifact import Agent, Context, Runtime, When
from reactifact.step import Step


class Ticket(BaseModel):
    status: str
    text: str


class Out(BaseModel):
    text: str


class PickOpen(Step):
    produces = (Out,)

    async def run(
        self, ticket: Annotated[Ticket, When(lambda t: t.status == "open")]
    ) -> None:
        self.effects.upsert(Out(text=ticket.text), id=f"out:{ticket.text}")


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

    async def run(self, ticket: Ticket) -> None:
        self.effects.upsert(Out(text=ticket.text), id=f"declared:{ticket.text}")


class DeclaredAgent(Agent):
    steps = [DeclaredConsume()]


def test_annotated_when_works_in_explicit_consumes():
    context = Context()
    context.create(Ticket(status="open", text="yes"))
    context.create(Ticket(status="closed", text="no"))

    asyncio.run(Runtime(context, agents=[DeclaredAgent()]).arun())

    assert {o.data.text for o in context.list_artifacts(Out)} == {"yes"}
