import asyncio

from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    Runtime,
    create_agent,
    produce,
)


class Input(BaseModel):
    text: str


class Output(BaseModel):
    text: str


@produce(Output)
async def echo(call):
    if not call.inputs:
        return None
    call.effects.create(Output(text=call.inputs[-1].data.text))
    return None


def test_create_agent_builds_container_without_subclass():
    agent = create_agent(
        name="echo",
        consumes=[Consume(Input)],
        produces=[echo],
    )
    assert agent.name == "echo"
    assert len(agent.triggers) == 2
    assert agent.matches.__self__ is agent


def test_create_agent_reacts():
    ctx = Context()
    runtime = Runtime(
        ctx, agents=[create_agent("echo", consumes=[Consume(Input)], produces=[echo])]
    )
    ctx.create(Input(text="hello"))
    asyncio.run(runtime.arun())

    out = ctx.latest(Output)
    assert out is not None
    assert out.data.text == "hello"


def test_context_latest_returns_most_recent():
    ctx = Context()
    ctx.create(Input(text="first"))
    ctx.create(Input(text="second"))

    latest = ctx.latest(Input)
    assert latest is not None
    assert latest.data.text == "second"


def test_context_latest_none_when_missing():
    ctx = Context()
    assert ctx.latest(Output) is None


def test_warn_no_runs_once(capsys):
    ctx = Context()
    ctx.create(Input(text="quiet"))
    runtime = Runtime(
        ctx,
        agents=[create_agent("echo", consumes=[Consume(Output)], produces=[echo])],
    )
    asyncio.run(runtime.arun())

    err = capsys.readouterr().err
    assert "None of the 1 agents ran" in err
    assert "consume" in err

    asyncio.run(runtime.arun())
    assert "None of the 1 agents ran" not in capsys.readouterr().err
