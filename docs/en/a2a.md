# A2A (Agent2Agent)

[Agent2Agent](https://a2a-protocol.org) is an open protocol for agent-to-agent
interop: an **Agent Card** for discovery, **Tasks** with a lifecycle, and
**Messages** carrying text/file/data parts, over JSON-RPC + SSE. `reactifact.a2a`
speaks it in both directions — **call** a remote agent, or **serve** your own —
with no extra dependency (the client uses `httpx`, the server FastAPI from the
existing `web` extra, imported lazily).

The JSON-RPC binding implemented here is the A2A `0.3` wire shape (camelCase
fields, `kind`-discriminated parts).

## Call a remote agent

`A2AClient` is the protocol surface; `A2AAgentTool` wraps it as a reactifact
`Tool`, so an `LLMAgent` can delegate to a remote agent like any other tool:

```python
from reactifact.a2a import A2AClient, A2AAgentTool

client = A2AClient("https://agent.example.com/a2a", headers={"x-api-key": "…"})
card = await client.fetch_agent_card()          # AgentCard
task = await client.send("Summarise the incident")
print(task.status.state, task.artifacts[0].parts[0].text)

researcher = A2AAgentTool(client, name="researcher")
# agent = create_agent("lead", consumes=[Consume(Question)], produces=[...researcher...])
```

`A2AClient` also exposes `stream(message)` (an async iterator of task/message
events over SSE), `get_task(id)` and `cancel_task(id)`.

## Run a remote agent as a node

`A2AAgentTool` is for an LLM loop. To have the **runtime schedule** a remote
agent alongside local ones — the analogue of LangGraph's `A2ARemoteGraph` — use
`remote_agent(...)`: a first-class node that consumes a local artifact, calls
`message/send`, and produces a local artifact, so provenance, budget, guardrails
and eval apply as usual:

```python
from reactifact import Consume, Runtime
from reactifact.a2a import remote_agent

translator = remote_agent(
    "translator",
    "https://agent.example.com/a2a",
    consumes=[Consume(Question)],
    output_type=Answer,          # built as Answer(text=reply)
)
Runtime(ctx, agents=[translator, …]).arun()
```

A remote task in `input-required` becomes a local `PendingQuestion`; a later
`context.resume(...)` continues the *same* remote task and lands the final
artifact. Pass `message_of=` (input → text) and `build_output=` (reply, input →
artifact) when the types have no `text` field. The lower-level `A2ARemoteProduce`
is the `Produce` behind it.

## Serve reactifact agents

`create_a2a_router(agents, ...)` returns a FastAPI router: an Agent Card plus a
JSON-RPC endpoint where **a `Task` is one conversation**:

```python
from fastapi import FastAPI
from reactifact.a2a import create_a2a_router

app = FastAPI()
app.include_router(create_a2a_router([RouteAgent(), …], name="ops-agent"))
```

It serves `GET /.well-known/agent-card.json` (and the legacy `/.well-known/agent.json`)
and `POST /` with `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`.

| A2A | reactifact |
| --- | --- |
| Agent Card (`skills`) | built from each agent's `produces` |
| Task | one conversation over a `Context` |
| `message/send` | seed via `create_message(ctx, text)`, run the agents to a fixpoint, read the reply with `reply(ctx, seed_id)` |
| `message/stream` (SSE) | the working task, a status update per `context.announce()` progress event, then the final task |
| `input-required` | the run paused on a `PendingQuestion`; the next `message/send` with the same `taskId` resumes it |
| Artifact | the reply text as a `Part` |

`create_message` and `reply` are the domain hooks (exactly like `reactifact.chat`):
without them the server seeds a `UserMessage` and replies with the last artifact
that has a `text` field. Pass your own to use the app's real input/output types:

```python
def create_message(ctx, text):
    return ctx.create(Question(text=text)).id

def reply(ctx, seed_id):
    answer = ctx.latest(Answer)
    return answer.data.text if answer else ""

app.include_router(create_a2a_router(AGENTS, create_message=create_message, reply=reply))
```

## Interop

The server is verified against the **official `a2a-sdk` client** (a `dev`-extra
test, not runtime): the reference client resolves our Agent Card and calls
`message/send`, receiving a `completed` task with the reply artifact — see
`tests/test_a2a_interop.py`. `a2a-sdk` is only a test dependency (its own
`a2a` group); the runtime keeps `httpx` + FastAPI and nothing else.

## Scope

Implemented: Agent Card, `message/send`, `message/stream` (SSE), `tasks/get`,
`tasks/cancel`, the `input-required` ↔ `PendingQuestion` mapping, and the
JSON-RPC binding `0.3`. Out of scope: push-notification config, `tasks/resubscribe`,
authenticated extended card, the gRPC and HTTP+JSON (REST) bindings, and any
`securitySchemes` (put auth in front of the router). Tasks are kept **in-process**
(like `reactifact.chat`) — persist the `Context` with a `Session` yourself for
durability.
