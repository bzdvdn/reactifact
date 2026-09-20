"""LoopBoundClient: clients rebind to the running loop (no 'loop is closed')."""

import asyncio

import httpx
from reactifact._httpx import LoopBoundClient
from reactifact.providers import (
    LLMRequest,
    Message,
    OpenAICompatProvider,
)


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    return httpx.MockTransport(handler)


def test_client_is_recreated_per_event_loop():
    holder = LoopBoundClient(lambda: httpx.AsyncClient(transport=_transport()))
    seen: list[int] = []

    async def scenario() -> None:
        seen.append(id(holder.get()))

    asyncio.run(scenario())
    asyncio.run(scenario())
    assert seen[0] != seen[1]  # a fresh client for each loop


def test_provider_survives_two_sequential_loops():
    """The whole point: one provider instance across repeated asyncio.run()."""
    provider = OpenAICompatProvider(base_url="https://llm.example/v1")

    async def call() -> str:
        response = await provider.complete(LLMRequest(messages=[Message.user("hi")]))
        return response.text

    # inject a MockTransport just for the requests; the loop-binding logic is
    # what is under test here
    provider._transport = _transport()  # type: ignore[attr-defined]
    first = asyncio.run(call())
    second = asyncio.run(call())  # previously: RuntimeError: Event loop is closed
    assert first == second == "ok"

    asyncio.run(provider.aclose())  # must not raise on a stale client


def test_aclose_on_a_dead_loop_is_a_noop():
    holder = LoopBoundClient(lambda: httpx.AsyncClient(transport=_transport()))

    async def make() -> None:
        holder.get()

    asyncio.run(make())  # client bound to a now-closed loop
    asyncio.run(holder.aclose())  # nothing to do, no exception


def test_injected_client_is_returned_as_is():
    fixed = httpx.AsyncClient(transport=_transport())
    holder = LoopBoundClient(lambda: httpx.AsyncClient(), client=fixed)

    async def scenario() -> None:
        assert holder.get() is fixed

    asyncio.run(scenario())
    asyncio.run(fixed.aclose())
