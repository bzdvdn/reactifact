import asyncio
import json

import httpx
from reactifact.providers import AnthropicProvider
from reactifact.providers.anthropic import anthropic_llm
from reactifact.providers.contracts import LLMRequest, Message

COMPLETION = {
    "content": [{"type": "text", "text": "привет от Claude"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 5, "output_tokens": 3},
}
SSE = (
    'event: message_start\ndata: {"type":"message_start"}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ку"}}\n\n'
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ку"}}\n\n'
    'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


def make_provider():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(
                200,
                content=SSE.encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=COMPLETION)

    return AnthropicProvider(
        api_key="test-key",
        max_tokens=256,
        transport=httpx.MockTransport(handler),
    )


def test_anthropic_complete_maps_contract():
    provider = make_provider()
    response = asyncio.run(
        provider.complete(
            LLMRequest(
                messages=[
                    Message.system("будь краток"),
                    Message.user("привет"),
                ],
                temperature=0.1,
            )
        )
    )
    assert response.text == "привет от Claude"
    assert response.finish_reason == "end_turn"


def test_anthropic_stream_text_deltas():
    provider = make_provider()

    async def collect():
        return [
            chunk
            async for chunk in provider.stream(
                LLMRequest(messages=[Message.user("hi")])
            )
        ]

    chunks = asyncio.run(collect())
    assert [c.text for c in chunks] == ["ку", "ку"]


def test_anthropic_stream_terminal_chunk_carries_usage():
    sse = (
        'event: message_start\ndata: {"type":"message_start",'
        '"message":{"usage":{"input_tokens":6}}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ку"}}\n\n'
        'event: message_delta\ndata: {"type":"message_delta",'
        '"usage":{"output_tokens":4}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    provider = AnthropicProvider(
        api_key="test-key",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                content=sse.encode(),
                headers={"content-type": "text/event-stream"},
            )
        ),
    )

    async def collect():
        return [
            c async for c in provider.stream(LLMRequest(messages=[Message.user("hi")]))
        ]

    chunks = asyncio.run(collect())
    assert [c.text for c in chunks] == ["ку", ""]
    assert chunks[-1].usage == {"prompt_tokens": 6, "completion_tokens": 4}


def test_anthropic_payload_shape():
    provider = make_provider()
    payload = provider._payload(
        LLMRequest(
            messages=[
                Message.system("SYS"),
                Message.user("U"),
            ],
            max_tokens=99,
        ),
        stream=False,
    )
    assert payload["model"] == "claude-3-5-sonnet-latest"
    assert payload["max_tokens"] == 99
    assert payload["system"] == "SYS"
    assert payload["messages"] == [{"role": "user", "content": "U"}]


def test_anthropic_payload_uses_provider_default_temperature():
    provider = make_provider()
    provider.temperature = 0.2
    payload = provider._payload(LLMRequest(messages=[Message.user("U")]), stream=False)
    # max_tokens always present for Anthropic (API requires it) — provider default.
    assert payload["max_tokens"] == 256
    assert payload["temperature"] == 0.2


def test_anthropic_payload_omits_temperature_when_unset():
    provider = make_provider()
    provider.temperature = None
    payload = provider._payload(LLMRequest(messages=[Message.user("U")]), stream=False)
    assert "temperature" not in payload
    assert payload["max_tokens"] == 256  # required by the API, provider default


def test_anthropic_llm_factory_requires_key():
    assert anthropic_llm() is None  # without ANTHROPIC_API_KEY — None
