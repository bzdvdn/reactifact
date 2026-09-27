"""Gemini provider: native chat contract, stream, image modal, env factory."""

import asyncio
import json

import httpx
from reactifact.providers import LLMRequest, Message, gemini_llm
from reactifact.providers.gemini import GeminiImageProvider, GeminiProvider

COMPLETION = {
    "candidates": [
        {
            "content": {"parts": [{"text": "hello from gemini"}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 4,
        "candidatesTokenCount": 3,
        "totalTokenCount": 7,
    },
}
IMAGE_COMPLETION = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {"text": "here you go"},
                    {
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": "iVBORw0KGgo=",  # not a real png, just bytes
                        }
                    },
                ]
            },
            "finishReason": "STOP",
        }
    ],
}
SSE = (
    'data: {"candidates": [{"content": {"parts": [{"text": "ку"}]}}]}\n\n'
    'data: {"candidates": [{"content": {"parts": [{"text": "ку"}]}}]}\n\n'
    'data: {"candidates": [{"content": {"parts": [{"text": ""}]}}]}\n\n'
)


def build_provider():
    return GeminiProvider(
        api_key="gem-key",
        model="gemini-2.0-flash",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=COMPLETION)),
    )


def test_complete_text_and_usage():
    provider = build_provider()
    resp = asyncio.run(
        provider.complete(LLMRequest(messages=[Message.user("hi")], temperature=0.2))
    )
    assert resp.text == "hello from gemini"
    assert resp.finish_reason == "STOP"
    assert resp.usage["total_tokens"] == 7


def test_calls_generate_content_endpoint():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("x-goog-api-key")
        return httpx.Response(200, json=COMPLETION)

    provider = GeminiProvider(
        api_key="gem-key",
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(
        provider.complete(LLMRequest(messages=[Message.user("hi")], temperature=0.1))
    )
    assert "generateContent" in seen["url"]
    assert seen["auth"] == "gem-key"  # raw key in x-goog-api-key
    assert seen["body"]["contents"][0]["role"] == "user"
    assert seen["body"]["generationConfig"]["temperature"] == 0.1


def test_gemini_payload_uses_provider_defaults_and_omits_unset():
    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=COMPLETION)

    seen: dict = {}
    provider = GeminiProvider(
        api_key="gem-key",
        temperature=0.4,
        max_tokens=777,
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(provider.complete(LLMRequest(messages=[Message.user("hi")])))
    gen = seen["body"]["generationConfig"]
    assert gen["temperature"] == 0.4
    assert gen["maxOutputTokens"] == 777

    seen2: dict = {}
    empty = GeminiProvider(
        api_key="gem-key",
        transport=httpx.MockTransport(
            lambda req: (
                seen2.update(body=json.loads(req.content))
                or httpx.Response(200, json=COMPLETION)
            )
        ),
    )
    asyncio.run(empty.complete(LLMRequest(messages=[Message.user("hi")])))
    gen2 = seen2["body"]["generationConfig"]
    assert "temperature" not in gen2
    assert "maxOutputTokens" not in gen2


def test_system_role_goes_to_system_instruction():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
        assert body["contents"][0]["role"] == "user"
        return httpx.Response(200, json=COMPLETION)

    provider = GeminiProvider(api_key="k", transport=httpx.MockTransport(handler))
    asyncio.run(
        provider.complete(
            LLMRequest(
                messages=[
                    Message.system("be brief"),
                    Message.user("hi"),
                ],
                temperature=0,
            )
        )
    )


def test_stream_yields_deltas():
    provider = GeminiProvider(
        api_key="k",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                content=SSE.encode(),
                headers={"content-type": "text/event-stream"},
            )
        ),
    )

    async def collect():
        chunks = []
        async for c in provider.stream(LLMRequest(messages=[Message.user("hi")])):
            chunks.append(c)
        return chunks

    chunks = asyncio.run(collect())
    assert [c.text for c in chunks] == ["ку", "ку"]


def test_stream_terminal_chunk_carries_usage():
    sse = (
        'data: {"candidates": [{"content": {"parts": [{"text": "ку"}]}}]}\n\n'
        'data: {"candidates": [], "usageMetadata": {"promptTokenCount": 8, '
        '"candidatesTokenCount": 5}}\n\n'
    )
    provider = GeminiProvider(
        api_key="k",
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
    assert chunks[-1].usage == {"prompt_tokens": 8, "completion_tokens": 5}


def test_gemini_image_provider():
    provider = GeminiImageProvider(
        api_key="k",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=IMAGE_COMPLETION)
        ),
    )
    data = asyncio.run(provider.generate("a robot"))
    assert data is not None
    assert data[:4] == b"\x89PNG"


def test_gemini_env_factory_raw_key():
    import os

    saved = {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "GEMINI_AUTH_SCHEME")}
    try:
        os.environ["GEMINI_API_KEY"] = "env-gem"
        os.environ["GEMINI_AUTH_SCHEME"] = ""
        provider = gemini_llm()
    finally:
        for k in saved:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]
    assert provider is not None
    assert provider._headers["x-goog-api-key"] == "env-gem"
