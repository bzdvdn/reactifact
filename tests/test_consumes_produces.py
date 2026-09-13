import asyncio

import pytest
from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Patch, Produce, Runtime


class Input(BaseModel):
    text: str


class Output(BaseModel):
    text: str


# --- Imperative agent (as before, run is overridden) ---
class SimpleAgent(Agent):
    consumes = [Consume(Input)]
    produces = [Produce(Output)]

    async def run(self, event, context):
        data = context.get(event.artifact_id)
        if data is None:
            return None
        return Patch().create(Output(text=data.data.text.upper()))


def test_auto_triggers_from_consumes():
    ctx = Context()
    runtime = Runtime(ctx, agents=[SimpleAgent()])

    agent = runtime.agents[0]
    assert len(agent.triggers) == 2  # created and updated

    ctx.create(Input(text="hello"))
    asyncio.run(runtime.arun())

    outputs = ctx.list_artifacts(Output)
    assert len(outputs) == 1
    assert outputs[0].data.text == "HELLO"


# --- Declarative agent (automatic run based on Produce) ---
async def make_upper(context, inputs):
    if not inputs:
        return []
    return [Output(text=inputs[0].data.text.upper())]


def test_produce_factory_kwarg_was_removed():
    """`Produce(..., factory=...)` (deprecated since 0.5) is gone entirely as
    of the 1.0 API freeze — use the `@produce` decorator instead."""
    with pytest.raises(TypeError):
        Produce(Output, factory=make_upper)  # type: ignore[call-arg]
