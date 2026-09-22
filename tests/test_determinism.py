"""Deterministic runs: strict ids + `verify_run` reproducibility check."""

import asyncio

from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    Runtime,
    RuntimeResources,
    create_agent,
    produce,
)
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse
from reactifact.replay import ReplayLLM, counter_ids, verify_run


class Q(BaseModel):
    text: str


class R(BaseModel):
    text: str


@produce(R)
async def auto_id_produce(call):
    question = call.trigger
    if question is None:
        return None
    call.effects.create(R(text="answer"))  # no explicit id → injected factory
    return None


@produce(R)
async def random_data_produce(call):
    import uuid

    call.effects.create(R(text=str(uuid.uuid4())), id="r:1")  # stable id, random data
    return None


def _agent(produce_obj) -> object:
    return create_agent("a", consumes=[Consume(Q)], produces=[produce_obj])


async def _build(agent, resources):
    context = Context(resources=resources)
    context.create(Q(text="q"))  # also auto-id
    await Runtime(context, agents=[agent]).arun()
    return context


def test_counter_ids_is_deterministic():
    a, b = counter_ids(), counter_ids()
    assert [a("Foo"), a("Foo")] == [b("Foo"), b("Foo")] == ["foo:0000", "foo:0001"]


def test_verify_run_reproducible_with_strict_ids():
    agent = _agent(auto_id_produce)

    async def build(resources):
        return await _build(agent, resources)

    report = asyncio.run(verify_run(build))
    assert report.ok, report.hashes
    assert len(set(report.hashes)) == 1


def test_verify_run_detects_uuid_nondeterminism():
    agent = _agent(auto_id_produce)

    async def build(resources):
        resources.id_factory = None  # force uuid ids — the run is no longer stable
        return await _build(agent, resources)

    report = asyncio.run(verify_run(build))
    assert not report.ok


def test_verify_run_detects_nondeterministic_data():
    agent = _agent(random_data_produce)

    async def build(resources):
        return await _build(agent, resources)

    report = asyncio.run(verify_run(build, repeat=3))
    assert not report.ok
    assert len(report.hashes) == 3


def test_verify_run_rejects_repeat_below_two():
    async def build(resources):
        return Context()

    try:
        asyncio.run(verify_run(build, repeat=1))
    except ValueError as exc:
        assert "repeat" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for repeat < 2")


# --- full flow: recorded model calls replayed across runs --------------------- #


class ScriptedLLM(LLMProvider):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text=self.responses.pop(0) if self.responses else "{}")

    async def stream(self, request):
        yield LLMResponse(text="")


class Body(BaseModel):
    text: str


@produce(Body)
async def llm_produce(call):
    from reactifact.structured import structured_llm

    question = call.trigger
    if question is None:
        return None
    body = await structured_llm(call.context, schema=Body, user=question.data.text)
    call.effects.create(body or Body(text="none"))
    return None


def test_verify_run_with_replayed_model(tmp_path):
    recording = tmp_path / "calls.jsonl"
    agent = _agent(llm_produce)

    async def build(resources):
        return await _build(agent, resources)

    # record once against a "real" provider…
    asyncio.run(
        build(
            RuntimeResources(
                llm=ReplayLLM(
                    recording, mode="record", inner=ScriptedLLM(['{"text":"hi"}'])
                ),
                id_factory=counter_ids(),
            )
        )
    )
    # …then every verify run replays it and must hash identically
    report = asyncio.run(verify_run(build, recording=recording))
    assert report.ok, report.hashes
    assert report.recording == str(recording)
