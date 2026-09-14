# MCP

reactifact speaks [MCP](https://modelcontextprotocol.io) both ways: call tools
from an external MCP server as ordinary `Tool`s, or expose reactifact's own
`Tool`s (and a running `Context`) as an MCP server for Claude Desktop, Claude
Code, or another agent to call into. Both require the `mcp` extra:

```bash
pip install "reactifact[mcp]"
```

Nothing in reactifact's core imports `mcp` — a bare `pip install reactifact`
never pulls in the SDK; `reactifact.mcp` raises a readable `ImportError` with
the install hint if you use it without the extra.

## Client: call remote MCP tools

`mcp_stdio_tools`/`mcp_http_tools` connect to an MCP server and yield its
tools as a `list[Tool]` — the same `Tool` contract `ToolUse`/`LLMAgent` already
take, so a remote MCP tool and a local `@tool` function are interchangeable:

```python
from reactifact import Consume, create_agent
from reactifact.mcp import mcp_stdio_tools
from reactifact.tool_use import ToolUse

async with mcp_stdio_tools(
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
) as tools:
    fs_agent = create_agent(
        "fs",
        consumes=[Consume(Question)],
        produces=[ToolUse("Answer questions about files in /tmp.", tools)],
    )
    # tools (and fs_agent) stay callable for the life of the `async with` block
```

`mcp_http_tools(url, headers=...)` connects over streamable HTTP the same
way — pass `headers` for a server that requires a static credential (e.g.
`{"Authorization": "Bearer ..."}`). Both are thin wrappers over
`mcp.ClientSession` — use `mcp_tools(session)` directly if you're managing
the session yourself (a custom transport, …).

### OAuth (client_credentials)

For a server that requires OAuth rather than a static header,
`oauth_client_credentials` builds an `auth=` value for `mcp_http_tools` using
the `client_credentials` grant — machine-to-machine, no browser or human
consent involved:

```python
from reactifact.mcp import mcp_http_tools, oauth_client_credentials

auth = oauth_client_credentials(
    "https://mcp.example.com",
    client_id="...",
    client_secret="...",
    issuer="https://auth.example.com",  # the authorization server that issued them
)
async with mcp_http_tools("https://mcp.example.com", auth=auth) as tools:
    ...
```

`issuer` pins the token exchange to that specific authorization server's
discovered metadata, so a compromised or misconfigured MCP server can't
redirect the credential exchange elsewhere. Tokens are cached in memory for
the life of the `auth` object (`InMemoryTokenStorage`); pass your own
`storage=` to persist them across restarts.

This covers `client_credentials` only — the flow where the agent itself
holds the credential. The authorization-code flow (a person granting consent
through a browser redirect) needs a `redirect_handler`/`callback_handler`
wired to whatever hosts the app, which is host-specific plumbing outside
this library's scope; use `mcp.client.auth.OAuthClientProvider` directly
for that.

## Server: expose reactifact as MCP

`create_mcp_server` builds an `mcp.server.mcpserver.MCPServer` from a list of
`Tool`s — hand-written `Tool` subclasses and `@tool`-decorated functions alike,
since both already carry a JSON schema (`Tool.schema`) that becomes the MCP
tool's real argument names and types, not one opaque `**kwargs`:

```python
from reactifact.mcp import create_mcp_server
from reactifact.tools import tool

@tool
async def search_catalog(query: str, limit: int = 10) -> str:
    """Searches the product catalog."""
    ...

server = create_mcp_server([search_catalog], name="my-app")
await server.run_stdio_async()
```

`destructive=True` on a `Tool` becomes the MCP tool's `destructiveHint`
annotation — the same signal Claude Desktop's own confirmation UI reads.

### Exposing a Context

Pass `context=` to also publish two read-only resources over MCP — an
external client can inspect a running reactifact app's state, including
provenance, the same way your own code does with `context.list_artifacts()`/
`context.get()`:

```python
server = create_mcp_server(tools, context=ctx, name="my-app")
```

- `context://artifacts/{artifact_type}` — every artifact of one type, newest
  first (e.g. `context://artifacts/Answer`).
- `context://artifact/{artifact_id}` — one artifact's current data and
  version.

### Mounting over HTTP

`server.streamable_http_app()` returns a Starlette app — mount it on the same
FastAPI app as `create_trace_router`/`create_chat_router`:

```python
app.mount("/mcp", server.streamable_http_app())
```

## Security model: no built-in authorization

reactifact has no built-in permission/authorization primitive — this applies
to MCP specifically and to the framework generally (§57 is explicitly
"planned", not "implemented"; see `docs/constitution.md`'s implementation
appendix). Concretely, for `create_mcp_server`:

- The two `context=` resources (`context://artifacts/...`, `context://artifact/...`)
  are **read-only** — an MCP client can inspect any artifact in the `Context`
  you pass, with no per-artifact-type or per-field redaction.
- **Tools are not sandboxed by MCP exposure.** Any `Tool` you hand to
  `create_mcp_server` is just as callable — and just as capable of mutating
  state — as it would be inside your own `ToolUse`/`LLMAgent` loop. If a tool
  can write to a database or call a paid API, an MCP client that can call it
  can do the same, exactly like a local tool-calling agent — MCP is a
  transport, not a permission boundary.

The host application owns access control: only pass a `Context` you're
willing to expose read-only in full, and only pass `Tool`s you're willing to
let any connected MCP client invoke. Gate destructive tools the same way you
would for a local `HITLLMAgent` (`ToolUseHITL`, §60) if a human approval step
is needed before a mutating tool actually runs.

## Errors: `ToolOutput.error` → MCP `is_error`

A `Tool` that returns `ToolOutput(error=...)` (or raises) surfaces to the MCP
caller as `is_error=True` with your message intact — reactifact raises the
SDK's own `ToolError` internally for this, the one exception type the SDK
does not mask down to a generic "Error executing tool …" for the caller.
