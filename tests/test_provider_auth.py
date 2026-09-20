"""Provider auth/proxy configurability (§: header name, scheme, proxy)."""

import asyncio

import httpx
from reactifact.providers import (
    AnthropicProvider,
    LLMRequest,
    Message,
    OpenAICompatEmbedder,
    OpenAICompatProvider,
)
from reactifact.providers.contracts import auth_value
from reactifact.providers.image import OpenAICompatImageProvider

COMPLETION = {
    "choices": [{"message": {"content": "ok", "finish_reason": "stop"}}],
    "usage": {"total_tokens": 1},
}


def captured(headers: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(dict(request.headers))
        return httpx.Response(200, json=COMPLETION)

    return httpx.MockTransport(handler)


def run(coro):
    return asyncio.run(coro)


def test_auth_value_schemes():
    assert auth_value("SEC", None) == "SEC"
    assert auth_value("SEC", "Bearer") == "Bearer SEC"
    assert auth_value("SEC", "OAuth") == "OAuth SEC"
    assert auth_value("SEC", "api-key") == "api-key SEC"


def test_chat_auth_default_bearer():
    captured_headers: list[dict] = []
    provider = OpenAICompatProvider(
        base_url="https://llm.example/v1",
        api_key="sk-123",
        transport=captured(captured_headers),
    )
    run(provider.complete(LLMRequest(messages=[Message.user("hi")], temperature=0)))
    assert captured_headers[0]["authorization"] == "Bearer sk-123"


def test_chat_auth_custom_header_and_raw_key():
    captured_headers: list[dict] = []
    provider = OpenAICompatProvider(
        base_url="https://llm.example/v1",
        api_key="rt-456",
        auth_header="X-Api-Key",
        auth_scheme=None,
        transport=captured(captured_headers),
    )
    run(provider.complete(LLMRequest(messages=[Message.user("hi")], temperature=0)))
    assert captured_headers[0]["x-api-key"] == "rt-456"
    assert "authorization" not in captured_headers[0]


def test_chat_auth_custom_scheme():
    captured_headers: list[dict] = []
    provider = OpenAICompatProvider(
        base_url="https://llm.example/v1",
        api_key="tok",
        auth_scheme="OAuth",
        transport=captured(captured_headers),
    )
    run(provider.complete(LLMRequest(messages=[Message.user("hi")], temperature=0)))
    assert captured_headers[0]["authorization"] == "OAuth tok"


def test_chat_extra_headers_win_over_default_auth():
    provider = OpenAICompatProvider(
        base_url="https://llm.example/v1",
        api_key="sk-x",
        extra_headers={"Authorization": "Bearer custom"},
        transport=captured([]),
    )
    headers = provider._headers
    assert headers["Authorization"] == "Bearer custom"


def test_embedder_auth_header():
    captured_headers: list[dict] = []

    class _Transport(httpx.MockTransport):
        pass

    embedder = OpenAICompatEmbedder(
        base_url="https://llm.example/v1",
        api_key="rt-1",
        auth_header="X-Api-Key",
        auth_scheme="Token",
        transport=httpx.MockTransport(
            lambda req: (
                captured_headers.append(dict(req.headers)),
                httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]}),
            )[1]
        ),
    )
    run(embedder.embed(["x"]))
    assert captured_headers[0]["x-api-key"] == "Token rt-1"


def test_anthropic_default_raw_x_api_key():
    captured_headers: list[dict] = []
    provider = AnthropicProvider(
        api_key="sk-ant",
        transport=httpx.MockTransport(
            lambda req: (
                captured_headers.append(dict(req.headers)),
                httpx.Response(
                    200,
                    json={"content": [{"type": "text", "text": "hi"}]},
                ),
            )[1]
        ),
    )
    run(provider.complete(LLMRequest(messages=[Message.user("hi")], temperature=0)))
    assert captured_headers[0]["x-api-key"] == "sk-ant"


def test_image_auth_header():
    captured_headers: list[dict] = []
    provider = OpenAICompatImageProvider(
        api_key="img-1",
        auth_header="X-API-Token",
        auth_scheme=None,
        transport=httpx.MockTransport(
            lambda req: (
                captured_headers.append(dict(req.headers)),
                httpx.Response(
                    200,
                    json={"data": [{"b64_json": "aGVsbG8="}]},
                ),
            )[1]
        ),
    )
    run(provider.generate("a robot"))
    assert captured_headers[0]["x-api-token"] == "img-1"


def test_proxy_is_forwarded():
    provider = OpenAICompatProvider(
        base_url="https://llm.example/v1",
        api_key="sk-1",
        proxy="http://proxy.example:8080",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=COMPLETION)),
    )
    assert provider._proxy == "http://proxy.example:8080"

    # httpx does not expose .proxy; the wiring contract is that a proxy URL is
    # forwarded to the AsyncClient and the client builds and caches cleanly.
    # `_get_client()` needs a running loop (the client is loop-bound).
    async def scenario() -> None:
        client = provider._get_client()
        assert client is provider._get_client()

    asyncio.run(scenario())


def test_env_knobs_are_read(tmp_path):
    import os

    keys = (
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_PROXY",
        "OPENAI_AUTH_HEADER",
        "OPENAI_AUTH_SCHEME",
    )
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["OPENAI_BASE_URL"] = "https://llm.example/v1"
        os.environ["OPENAI_API_KEY"] = "sk-env"
        os.environ["OPENAI_PROXY"] = "http://proxy.example:8080"
        os.environ["OPENAI_AUTH_HEADER"] = "X-Api-Key"
        os.environ["OPENAI_AUTH_SCHEME"] = "Bearer"
        from reactifact.providers import llm_from_env

        provider = llm_from_env()
    finally:
        for k in keys:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]
    assert provider is not None
    assert provider._proxy == "http://proxy.example:8080"
    assert provider._headers["X-Api-Key"] == "Bearer sk-env"


def test_llm_from_env_forwards_extra_overrides():
    """`temperature`/`max_tokens` (and other OpenAICompatProvider kwargs) were
    previously dropped silently by llm_from_env — now they pass through."""
    import os

    saved = os.environ.get("OPENAI_BASE_URL")
    try:
        os.environ["OPENAI_BASE_URL"] = "https://llm.example/v1"
        from reactifact.providers import llm_from_env

        provider = llm_from_env(temperature=0.2, max_tokens=256)
    finally:
        if saved is None:
            os.environ.pop("OPENAI_BASE_URL", None)
        else:
            os.environ["OPENAI_BASE_URL"] = saved
    assert provider is not None
    assert provider.temperature == 0.2
    assert provider.max_tokens == 256


def test_from_env_prefers_openrouter_key():
    import os

    keys = ("OPENROUTER_API_KEY", "OPENAI_BASE_URL")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["OPENROUTER_API_KEY"] = "or-key"
        os.environ["OPENAI_BASE_URL"] = "https://llm.example/v1"
        from reactifact.providers import from_env

        provider = from_env(max_tokens=128)
    finally:
        for k in keys:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]
    assert provider is not None
    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.max_tokens == 128


def test_from_env_falls_back_to_openai_base_url():
    import os

    keys = ("OPENROUTER_API_KEY", "OPENAI_BASE_URL")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ["OPENAI_BASE_URL"] = "https://llm.example/v1"
        from reactifact.providers import from_env

        provider = from_env()
    finally:
        for k in keys:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]
    assert provider is not None
    assert provider.base_url == "https://llm.example/v1"


def test_from_env_none_when_unconfigured():
    import os

    keys = ("OPENROUTER_API_KEY", "OPENAI_BASE_URL")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        from reactifact.providers import from_env

        assert from_env() is None
    finally:
        for k in keys:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]


def test_env_auth_scheme_empty_is_raw_key():
    import os

    saved = {
        k: os.environ.get(k)
        for k in (
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
            "OPENAI_AUTH_SCHEME",
        )
    }
    try:
        os.environ["OPENAI_BASE_URL"] = "https://llm.example/v1"
        os.environ["OPENAI_API_KEY"] = "raw"
        os.environ["OPENAI_AUTH_SCHEME"] = ""
        from reactifact.providers import llm_from_env

        provider = llm_from_env()
    finally:
        for k in saved:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]
    assert provider is not None
    assert provider._headers["Authorization"] == "raw"
