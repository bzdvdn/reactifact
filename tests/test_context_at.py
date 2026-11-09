import asyncio

from pydantic import BaseModel
from reactifact import Agent, Context, Runtime
from reactifact.step import Step


class A(BaseModel):
    text: str


class Out(BaseModel):
    text: str


class Make(Step):
    consumes = (A,)
    produces = (Out,)

    async def run(self, a: A) -> None:
        self.effects.upsert(Out(text=a.text), id="out")


class Flow(Agent):
    steps = [Make()]


def test_artifact_and_context_immutable_version_reads():
    context = Context()
    a = context.create(A(text="one"))
    runtime = Runtime(context, agents=[Flow()])
    asyncio.run(runtime.arun())
    first_version = context.version

    context.update(a.id, A(text="two"))
    asyncio.run(runtime.arun())
    second_version = context.version

    live = context.get("out")
    assert live is not None
    assert live.data.text == "two"
    assert live.at(0).text == "one"
    assert live.at(1).text == "two"

    snapshot = context.at(first_version)
    assert snapshot.version == first_version
    assert snapshot.get("out").data.text == "one"
    assert context.get("out").data.text == "two"  # live unchanged

    latest = context.at(second_version)
    assert latest.get("out").data.text == "two"
