import asyncio
import json

import httpx
from reactifact.providers import (
    LLMRequest,
    LLMResponse,
    Message,
    OpenAICompatProvider,
    llm_from_env,
)

COMPLETION = {
    "choices": [{"message": {"content": "привет мир"}, "finish_reason": "stop"}],
    "usage": {"total_tokens": 3},
}
SSE_CHUNKS = (
    'data: {"choices": [{"delta": {"content": "ку"}}]}\n\n'
    'data: {"choices": [{"delta": {"content": "ку"}}]}\n\n'
    'data: {"choices": [{"delta": {}}]}\n\n'
    "data: [DONE]\n\n"
)


def make_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(
                200,
                content=SSE_CHUNKS.encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=COMPLETION)

    return httpx.MockTransport(handler)


def build_provider():
    return OpenAICompatProvider(
        base_url="https://llm.example/v1",
        model="test-model",
        transport=make_transport(),
    )


def test_complete_non_stream():
    provider = build_provider()
    response = asyncio.run(
        provider.complete(LLMRequest(messages=[Message.user("hi")], temperature=0.1))
    )
    assert isinstance(response, LLMResponse)
    assert response.text == "привет мир"
    assert response.finish_reason == "stop"
    assert response.usage["total_tokens"] == 3


def test_stream_chunks_aggregate():
    provider = build_provider()

    async def collect():
        chunks = []
        async for chunk in provider.stream(LLMRequest(messages=[Message.user("hi")])):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert [c.text for c in chunks] == ["ку", "ку"]


def test_stream_payload_asks_for_usage():
    provider = build_provider()
    streaming = provider._payload(
        LLMRequest(messages=[Message.user("hi")]), stream=True
    )
    assert streaming["stream_options"] == {"include_usage": True}
    non_stream = provider._payload(
        LLMRequest(messages=[Message.user("hi")]), stream=False
    )
    assert "stream_options" not in non_stream


def test_stream_terminal_chunk_carries_usage():
    sse = (
        'data: {"choices": [{"delta": {"content": "ку"}}]}\n\n'
        'data: {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}\n\n'
        "data: [DONE]\n\n"
    )
    provider = OpenAICompatProvider(
        base_url="https://llm.example/v1",
        model="test-model",
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
    assert chunks[-1].usage == {"prompt_tokens": 4, "completion_tokens": 2}


def test_payload_includes_model_and_format():
    provider = build_provider()
    payload = provider._payload(
        LLMRequest(
            messages=[Message.user("hi")],
            response_format={"type": "json_object"},
            max_tokens=10,
        ),
        stream=False,
    )
    assert payload["model"] == "test-model"
    assert payload["max_tokens"] == 10
    assert payload["response_format"] == {"type": "json_object"}


def test_payload_omits_unset_temperature_and_max_tokens():
    provider = build_provider()
    payload = provider._payload(LLMRequest(messages=[Message.user("hi")]), stream=False)
    assert "temperature" not in payload
    assert "max_tokens" not in payload


def test_payload_uses_provider_defaults():
    provider = build_provider()
    provider.temperature = 0.3
    provider.max_tokens = 512
    payload = provider._payload(LLMRequest(messages=[Message.user("hi")]), stream=False)
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 512


def test_payload_request_overrides_provider_default():
    provider = build_provider()
    provider.temperature = 0.3
    provider.max_tokens = 512
    payload = provider._payload(
        LLMRequest(messages=[Message.user("hi")], temperature=0.9, max_tokens=100),
        stream=False,
    )
    assert payload["temperature"] == 0.9
    assert payload["max_tokens"] == 100


def test_llm_from_env_empty():
    import os

    old = {k: os.environ.pop(k, None) for k in ("OPENAI_BASE_URL",)}
    try:
        assert llm_from_env() is None
    finally:
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v
