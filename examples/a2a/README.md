# a2a — an agent served over A2A, and called back

The smallest end-to-end [Agent2Agent](../../docs/en/a2a.md) app: a reactifact
agent exposed as an A2A server, and a client that both calls it directly and
schedules it as a **local node** (`remote_agent`). Fully deterministic — no LLM,
no network (everything runs in-process over `httpx.ASGITransport`).

```bash
.venv/bin/python -m examples.a2a.demo
```

```
A2A example — in-process, no network, no LLM

  Agent Card          name='upper-agent' skills=['upper']
  A2AClient.send    -> state=completed text='HELLO A2A'
  remote_agent node -> Answer(text='RUN VIA NODE')
  input-required    -> question='Which format should I use?'
  after resume      -> Answer(text='got it: markdown')
```

## Structure

```
a2a/
├── models.py    # Question (client-side) + Answer (server-side)
├── agents.py    # `upper` (one-shot) and `clarifier` (asks, then answers — HITL)
├── server.py    # FastAPI apps: create_a2a_router(agents) at the root
├── demo.py      # run_demo(): the whole conversation, in-process
└── __init__.py
```

## What it shows

- **Serving** — `create_a2a_router([upper_agent()])` turns any FastAPI app into
  an A2A server: an Agent Card (`/.well-known/agent-card.json`, skills from the
  agent's `produces`) and a JSON-RPC endpoint (`message/send`, `message/stream`,
  `tasks/get`, `tasks/cancel`).
- **Calling, directly** — `A2AClient.send("hello a2a")` returns the A2A `Task`:
  `status.state == "completed"`, the reply in `artifacts[0].parts[0].text`.
- **Calling, as a node** — `remote_agent("upper", client, consumes=[Consume(Question)],
  output_type=Answer)` is a first-class node the local `Runtime` schedules, so a
  remote agent composes with local ones (provenance, budget, guardrails apply).
- **HITL** — the `clarifier` agent answers the first message with a question, so
  its remote task is `input-required`; `remote_agent` surfaces that as a local
  `PendingQuestion`, and `context.resume(...)` continues the *same* remote task.

The two server apps are mounted at the root here; a real app can use
`create_a2a_router(agents, prefix="/a2a", url="https://host/a2a/")` and register
several agents on one app. Swapping the in-process transport for
`httpx.AsyncClient()` points the same client at a remote server.
