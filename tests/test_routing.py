"""`reactifact.routing` — provider failover: try providers in order, fall back
on failure (with an optional `should_fallback` predicate)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import BaseModel
from reactifact import Agent, Consume, Context, Produce, ProduceCall, Runtime
from reactifact.agents import create_agent
from reactifact.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    Message,
)
from reactifact.resources import RuntimeResources
from reactifact.routing import RouterLLM, retryable_only


class Boom(LLMProvider):
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.model = "boom"
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        raise self.exc

    async def stream(self, request: LLMRequest):
        raise self.exc
        yield  # pragma: no cover — makes this an async generator


class OK(LLMProvider):
    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.model = "ok"
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(text=self.text)

    async def stream(self, request: LLMRequest):
        yield LLMResponseChunk(text=self.text)


def _req(text: str = "hi") -> LLMRequest:
    return LLMRequest(messages=[Message.user(text)])


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.example/v1")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def test_falls_back_to_the_next_provider():
    async def go():
        primary = Boom(ConnectionError("down"))
        secondary = OK("second")
        seen: list = []
        router = RouterLLM(
            [primary, secondary], on_fallback=lambda p, e: seen.append((p, e))
        )
        response = await router.complete(_req())
        return primary, secondary, seen, response

    primary, secondary, seen, response = asyncio.run(go())
    assert response.text == "second"
    assert primary.calls == 1 and secondary.calls == 1
    assert [provider for provider, _ in seen] == [primary]


def test_all_failing_providers_raise_the_last_error():
    router = RouterLLM([Boom(ValueError("first")), Boom(ValueError("last"))])

    with pytest.raises(ValueError, match="last"):
        asyncio.run(router.complete(_req()))


def test_retryable_only_does_not_fall_back_on_a_4xx():
    async def go():
        secondary = OK("b")
        router = RouterLLM(
            [Boom(_http_error(400)), secondary], should_fallback=retryable_only
        )
        with pytest.raises(httpx.HTTPStatusError):
            await router.complete(_req())
        return secondary

    assert asyncio.run(go()).calls == 0


def test_retryable_only_falls_back_on_a_5xx():
    router = RouterLLM(
        [Boom(_http_error(503)), OK("recovered")], should_fallback=retryable_only
    )

    assert asyncio.run(router.complete(_req())).text == "recovered"


def test_retryable_only_falls_back_on_a_transport_error():
    router = RouterLLM(
        [Boom(httpx.ConnectError("no route")), OK("backup")],
        should_fallback=retryable_only,
    )

    assert asyncio.run(router.complete(_req())).text == "backup"


def test_empty_providers_is_an_error():
    with pytest.raises(ValueError, match="at least one"):
        RouterLLM([])


def test_stream_uses_the_first_provider():
    async def go():
        router = RouterLLM([OK("first"), OK("second")])
        return [chunk async for chunk in router.stream(_req())]

    assert [chunk.text for chunk in asyncio.run(go())] == ["first"]


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Ask(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call: ProduceCall) -> None:
        response = await call.context.resources.llm.complete(  # type: ignore[union-attr]
            LLMRequest(messages=[Message.user("hi")])
        )
        call.effects.create(Answer(text=response.text))


def test_router_works_inside_a_runtime():
    agent = create_agent("ask", consumes=[Consume(Question)], produces=[Ask()])
    resources = RuntimeResources(
        llm=RouterLLM([Boom(ConnectionError("primary down")), OK("answered")])
    )
    ctx = Context(resources=resources)
    ctx.create(Question(text="q"))

    asyncio.run(Runtime(ctx, agents=[agent]).arun())

    assert isinstance(agent, Agent)
    assert ctx.latest(Answer) is not None
    assert ctx.latest(Answer).data.text == "answered"
