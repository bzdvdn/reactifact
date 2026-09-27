"""Token/cost budgets: `Budget.max_tokens`/`max_cost`, `RunOutcome` outcomes,
`RunStats` usage totals, and tracer-independent counting (streaming included)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from reactifact import (
    Agent,
    Budget,
    Consume,
    Context,
    Produce,
    ProduceCall,
    RunOutcome,
    Runtime,
    RuntimeResources,
)
from reactifact.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    Message,
)


class Number(BaseModel):
    value: int


class UsageLLM(LLMProvider):
    """Reports a fixed usage per call and counts calls (no tracer involved)."""

    def __init__(self, prompt: int = 10, completion: int = 5) -> None:
        self.usage = {"prompt_tokens": prompt, "completion_tokens": completion}
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(text="{}", usage=dict(self.usage))

    async def stream(self, request: LLMRequest):
        yield LLMResponseChunk(text="x")


class Chatty(Produce[Number]):
    """Calls the LLM once per run, then chains a new Number."""

    artifact_type = Number

    async def produce(self, call: ProduceCall) -> None:
        await call.context.resources.llm.complete(  # type: ignore[union-attr]
            LLMRequest(messages=[Message.user("hi")])
        )
        trigger = call.trigger
        if trigger is None:
            return None
        call.effects.create(Number(value=trigger.data.value + 1))


class ChattyAgent(Agent):
    name = "chatty"
    consumes = [Consume(Number)]
    produces = [Chatty()]


class StreamUsageLLM(LLMProvider):
    """Usage arrives on a terminal streaming chunk."""

    def __init__(self, prompt: int = 7, completion: int = 3) -> None:
        self.usage = {"prompt_tokens": prompt, "completion_tokens": completion}
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(text="{}", usage=dict(self.usage))

    async def stream(self, request: LLMRequest):
        self.calls += 1
        yield LLMResponseChunk(text="he")
        yield LLMResponseChunk(text="llo", usage=dict(self.usage))


class Streaming(Produce[Number]):
    artifact_type = Number

    async def produce(self, call: ProduceCall) -> None:
        llm = call.context.resources.llm
        assert llm is not None
        async for _ in llm.stream(LLMRequest(messages=[Message.user("hi")])):
            pass
        trigger = call.trigger
        if trigger is None:
            return None
        call.effects.create(Number(value=trigger.data.value + 1))


class StreamingAgent(Agent):
    name = "streaming"
    consumes = [Consume(Number)]
    produces = [Streaming()]


def _price(model: str, prompt: int, completion: int) -> float:
    return (prompt + completion) * 0.001


def _run(
    agent: Agent,
    budget: Budget,
    llm: LLMProvider,
    *,
    pricer: object = None,
) -> Runtime:
    ctx = Context(resources=RuntimeResources(llm=llm, pricer=pricer))  # type: ignore[arg-type]
    runtime = Runtime(ctx, agents=[agent], budget=budget)
    ctx.create(Number(value=0))
    asyncio.run(runtime.arun())
    return runtime


def test_max_tokens_stops_and_reports_usage_without_a_tracer():
    llm = UsageLLM(prompt=10, completion=5)  # 15 tokens per call

    runtime = _run(ChattyAgent(), Budget(max_tokens=20), llm)

    assert runtime.outcome == RunOutcome.BUDGET_TOKENS_EXCEEDED
    assert runtime.last_stats is not None
    assert runtime.last_stats.total_tokens == 30
    assert (runtime.last_stats.prompt_tokens, runtime.last_stats.completion_tokens) == (
        20,
        10,
    )
    assert llm.calls == 2  # the over-budget 3rd generation never calls the LLM


def test_max_cost_uses_the_injected_pricer():
    llm = UsageLLM(prompt=10, completion=5)  # 15 tokens -> 0.015 per call

    runtime = _run(ChattyAgent(), Budget(max_cost=0.02), llm, pricer=_price)

    assert runtime.outcome == RunOutcome.BUDGET_COST_EXCEEDED
    assert runtime.last_stats is not None
    assert runtime.last_stats.cost == pytest.approx(0.03)
    assert llm.calls == 2


def test_max_cost_without_pricer_is_inert(caplog):
    llm = UsageLLM()

    runtime = _run(ChattyAgent(), Budget(max_cost=0.001, max_runs=2), llm)

    # cost stays 0 (no pricer) -> the run stops on max_runs, not cost
    assert runtime.outcome == RunOutcome.BUDGET_RUNS_EXCEEDED
    assert runtime.last_stats is not None
    assert runtime.last_stats.cost == 0.0
    assert "pricer is None" in caplog.text


def test_streaming_usage_counts_toward_the_token_budget():
    llm = StreamUsageLLM(prompt=7, completion=3)  # 10 tokens per streamed call

    runtime = _run(StreamingAgent(), Budget(max_tokens=15), llm)

    assert runtime.outcome == RunOutcome.BUDGET_TOKENS_EXCEEDED
    assert runtime.last_stats is not None
    assert runtime.last_stats.total_tokens == 20
    assert llm.calls == 2


def test_llm_wrapper_is_restored_after_the_turn():
    llm = UsageLLM()
    ctx = Context(resources=RuntimeResources(llm=llm))
    runtime = Runtime(ctx, agents=[ChattyAgent()], budget=Budget(max_tokens=1))
    ctx.create(Number(value=0))

    asyncio.run(runtime.arun())

    assert ctx.resources.llm is llm  # not left wrapped
