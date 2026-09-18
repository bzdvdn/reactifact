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


class Extra(BaseModel):
    text: str


class MultiCreateProduce(Produce[Output]):
    """A single produce whose body legitimately writes two artifact types —
    the case `also_creates` exists for."""

    artifact_type = Output
    also_creates = (Extra,)

    async def produce(self, call):
        if not call.inputs:
            return None
        self.effects.create(Output(text=call.inputs[0].data.text.upper()))
        self.effects.create(Extra(text=call.inputs[0].data.text.lower()))


class UndeclaredMultiCreateProduce(Produce[Output]):
    """Same body, but `Extra` is never declared — must be rejected."""

    artifact_type = Output

    async def produce(self, call):
        if not call.inputs:
            return None
        self.effects.create(Output(text=call.inputs[0].data.text.upper()))
        self.effects.create(Extra(text=call.inputs[0].data.text.lower()))


def test_also_creates_widens_the_agents_allowed_create_types():
    class MultiAgent(Agent):
        consumes = [Consume(Input)]
        produces = [MultiCreateProduce()]

    ctx = Context()
    runtime = Runtime(ctx, agents=[MultiAgent()])
    ctx.create(Input(text="hello"))
    asyncio.run(runtime.arun())

    assert [o.data.text for o in ctx.list_artifacts(Output)] == ["HELLO"]
    assert [e.data.text for e in ctx.list_artifacts(Extra)] == ["hello"]


def test_create_of_an_undeclared_type_is_rejected_even_without_also_creates():
    class UndeclaredMultiAgent(Agent):
        consumes = [Consume(Input)]
        produces = [UndeclaredMultiCreateProduce()]

    ctx = Context()
    runtime = Runtime(ctx, agents=[UndeclaredMultiAgent()])
    ctx.create(Input(text="hello"))
    with pytest.raises(ValueError, match="not declared in produces"):
        asyncio.run(runtime.arun())


class Passive(BaseModel):
    """Read as an input but should never wake the agent on its own —
    `Consume(wakes=False)`."""

    text: str


class RecordMixed(Produce[Output]):
    artifact_type = Output

    async def produce(self, call):
        self.effects.create(
            Output(text=",".join(sorted(i.data.text for i in call.inputs)))
        )


class MixedAgent(Agent):
    consumes = [Consume(Input), Consume(Passive, wakes=False)]
    produces = [RecordMixed()]


def test_consume_wakes_false_feeds_inputs_without_triggering():
    """For the declarative style (`consumes`/`produces`), `Consume(wakes=False)`
    decouples "what wakes me" from "what I read" without reaching for
    `Agent`'s separate `triggers=` override (still there, but meant for the
    imperative `run()`-override style with no `consumes` at all)."""
    ctx = Context()
    runtime = Runtime(ctx, agents=[MixedAgent()])
    agent = runtime.agents[0]

    assert len(agent.triggers) == 2  # created+updated, from Input only
    assert all(t.artifact_type is Input for t in agent.triggers)

    ctx.create(Passive(text="p"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(Output) == []  # Passive alone never wakes the agent

    ctx.create(Input(text="i"))
    asyncio.run(runtime.arun())
    outputs = ctx.list_artifacts(Output)
    assert len(outputs) == 1
    assert (
        outputs[0].data.text == "i,p"
    )  # Passive was still read once Input woke the agent


def test_debounce_without_wakes_is_rejected():
    """`debounce=True` only means anything for a `Consume` that also wakes
    the agent — the combination with `wakes=False` is meaningless, not a
    valid "quiet debounce" mode, so it's rejected at construction time
    rather than silently doing nothing."""
    with pytest.raises(ValueError, match="meaningless"):
        Consume(Input, wakes=False, debounce=True)
