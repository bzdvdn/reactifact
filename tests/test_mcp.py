"""MCP client/server, exercised over the real protocol via an in-memory
transport (`mcp.shared.memory`) — no subprocess, no network, but genuine
wire-format initialize/list_tools/call_tool/read_resource round-trips."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from reactifact import Context
from reactifact.mcp import (
    create_mcp_server,
    mcp_http_tools,
    mcp_tools,
    oauth_client_credentials,
)
from reactifact.tools import Tool, ToolOutput, tool

pytest.importorskip("mcp")

from mcp.shared.memory import create_client_server_memory_streams  # noqa: E402

from mcp import ClientSession  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def connected(server) -> AsyncGenerator[ClientSession, None]:
    """Runs `server` and a `ClientSession` over in-memory streams; both ends
    speak the real MCP protocol, just without a socket or subprocess."""
    async with create_client_server_memory_streams() as (
        client_streams,
        server_streams,
    ):
        client_read, client_write = client_streams
        server_read, server_write = server_streams

        async def run_server() -> None:
            await server._lowlevel_server.run(
                server_read,
                server_write,
                server._lowlevel_server.create_initialization_options(),
            )

        async with asyncio.TaskGroup() as tg:
            server_task = tg.create_task(run_server())
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                yield session
            server_task.cancel()


class Answer(BaseModel):
    text: str


@tool
async def add(a: int, b: int) -> str:
    """Adds two integers."""
    return str(a + b)


class AlwaysFails(Tool):
    name = "always_fails"
    description = "A tool that always reports an error."
    schema = {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict) -> ToolOutput:
        return ToolOutput(error="deliberately broken")


@tool(destructive=True)
async def delete_thing(thing_id: str) -> str:
    """Deletes a thing by id."""
    return f"deleted {thing_id}"


@tool
async def greet(name: str, loud: bool = False) -> str:
    """Greets someone, optionally loudly."""
    text = f"hello {name}"
    return text.upper() if loud else text


def test_mcp_server_exposes_tool_with_real_schema():
    server = create_mcp_server([add], name="test")

    async def scenario():
        async with connected(server) as session:
            listed = await session.list_tools()
            assert [t.name for t in listed.tools] == ["add"]
            schema = listed.tools[0].input_schema
            assert schema["properties"]["a"]["type"] == "integer"
            assert schema["properties"]["b"]["type"] == "integer"
            assert set(schema["required"]) == {"a", "b"}

            tools = await mcp_tools(session)
            assert len(tools) == 1
            result = await tools[0].execute({"a": 2, "b": 3})
            assert result.error == ""
            assert result.text == "5"

    run(scenario())


def test_mcp_tool_error_round_trips():
    server = create_mcp_server([AlwaysFails()], name="test")

    async def scenario():
        async with connected(server) as session:
            tools = await mcp_tools(session)
            result = await tools[0].execute({})
            assert result.error != ""
            assert "deliberately broken" in result.error

    run(scenario())


def test_mcp_destructive_annotation_round_trips():
    server = create_mcp_server([delete_thing], name="test")

    async def scenario():
        async with connected(server) as session:
            listed = await session.list_tools()
            assert listed.tools[0].annotations is not None
            assert listed.tools[0].annotations.destructive_hint is True

            tools = await mcp_tools(session)
            assert tools[0].destructive is True

    run(scenario())


def test_mcp_optional_arg_uses_own_default_when_omitted():
    server = create_mcp_server([greet], name="test")

    async def scenario():
        async with connected(server) as session:
            tools = await mcp_tools(session)
            (greet_tool,) = tools
            quiet = await greet_tool.execute({"name": "Ada"})
            assert quiet.text == "hello Ada"
            loud = await greet_tool.execute({"name": "Ada", "loud": True})
            assert loud.text == "HELLO ADA"

    run(scenario())


def test_mcp_server_exposes_context_as_resources():
    ctx = Context()
    a1 = ctx.create(Answer(text="first"))
    ctx.create(Answer(text="second"))
    server = create_mcp_server([], context=ctx, name="test")

    async def scenario():
        async with connected(server) as session:
            templates = await session.list_resource_templates()
            uris = {t.uri_template for t in templates.resource_templates}
            assert "context://artifacts/{artifact_type}" in uris
            assert "context://artifact/{artifact_id}" in uris

            listed = await session.read_resource("context://artifacts/Answer")
            body = listed.contents[0].text
            assert '"first"' in body or "first" in body
            assert "second" in body

            one = await session.read_resource(f"context://artifact/{a1.id}")
            assert "first" in one.contents[0].text

    run(scenario())


def test_mcp_http_tools_passes_headers_to_the_transport(monkeypatch):
    """`mcp_http_tools(url, headers=...)` must build an authenticated
    http client and hand it to the transport — regression guard for the
    auth gap (no way to reach a server behind Bearer/API-key auth) that
    this parameter closes."""
    import httpx2
    import mcp.client.streamable_http as streamable_http_module

    import mcp as mcp_pkg

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_streamable_http_client(
        url: str, *, http_client=None, **_: object
    ) -> AsyncGenerator[tuple[object, object], None]:
        captured["url"] = url
        captured["http_client"] = http_client
        yield (object(), object())

    class FakeSession:
        def __init__(self, read: object, write: object) -> None:
            pass

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[])

    monkeypatch.setattr(
        streamable_http_module, "streamable_http_client", fake_streamable_http_client
    )
    monkeypatch.setattr(mcp_pkg, "ClientSession", FakeSession)

    async def scenario() -> None:
        async with mcp_http_tools(
            "https://example.invalid/mcp", headers={"Authorization": "Bearer tok"}
        ) as tools:
            assert tools == []

    run(scenario())

    http_client = captured["http_client"]
    assert isinstance(http_client, httpx2.AsyncClient)
    assert http_client.headers["authorization"] == "Bearer tok"


def test_mcp_http_tools_without_headers_uses_the_default_client(monkeypatch):
    """No `headers=` -> `http_client=None` is passed through unchanged, so
    the SDK's own default client (with its own timeouts) is used."""
    import mcp.client.streamable_http as streamable_http_module

    import mcp as mcp_pkg

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_streamable_http_client(
        url: str, *, http_client=None, **_: object
    ) -> AsyncGenerator[tuple[object, object], None]:
        captured["http_client"] = http_client
        yield (object(), object())

    class FakeSession:
        def __init__(self, read: object, write: object) -> None:
            pass

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[])

    monkeypatch.setattr(
        streamable_http_module, "streamable_http_client", fake_streamable_http_client
    )
    monkeypatch.setattr(mcp_pkg, "ClientSession", FakeSession)

    async def scenario() -> None:
        async with mcp_http_tools("https://example.invalid/mcp") as tools:
            assert tools == []

    run(scenario())
    assert captured["http_client"] is None


def test_mcp_http_tools_passes_oauth_auth_to_the_transport(monkeypatch):
    """`mcp_http_tools(url, auth=...)` must build an `httpx2.AsyncClient`
    carrying the given auth (e.g. `oauth_client_credentials(...)`) and hand
    it to the transport — regression guard for the OAuth client_credentials
    gap (no way to reach a server that requires it) this parameter closes."""
    import httpx2
    import mcp.client.streamable_http as streamable_http_module
    from mcp.client.auth.extensions.client_credentials import (
        ClientCredentialsOAuthProvider,
    )

    import mcp as mcp_pkg

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_streamable_http_client(
        url: str, *, http_client=None, **_: object
    ) -> AsyncGenerator[tuple[object, object], None]:
        captured["http_client"] = http_client
        yield (object(), object())

    class FakeSession:
        def __init__(self, read: object, write: object) -> None:
            pass

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[])

    monkeypatch.setattr(
        streamable_http_module, "streamable_http_client", fake_streamable_http_client
    )
    monkeypatch.setattr(mcp_pkg, "ClientSession", FakeSession)

    auth = oauth_client_credentials(
        "https://example.invalid/mcp",
        client_id="cid",
        client_secret="secret",
        issuer="https://auth.example.invalid",
    )

    async def scenario() -> None:
        async with mcp_http_tools("https://example.invalid/mcp", auth=auth) as tools:
            assert tools == []

    run(scenario())

    http_client = captured["http_client"]
    assert isinstance(http_client, httpx2.AsyncClient)
    assert http_client.auth is auth
    assert isinstance(auth, ClientCredentialsOAuthProvider)


def test_oauth_client_credentials_defaults_to_in_memory_storage():
    from reactifact.mcp import InMemoryTokenStorage

    auth = oauth_client_credentials(
        "https://example.invalid/mcp",
        client_id="cid",
        client_secret="secret",
        issuer="https://auth.example.invalid",
    )
    assert isinstance(auth.context.storage, InMemoryTokenStorage)
