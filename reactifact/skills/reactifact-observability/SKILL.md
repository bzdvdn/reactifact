---
name: reactifact-observability
description: Observe and persist reactifact runs — traces and TraceStore, Prometheus metrics, session save/resume and checkpoints, deterministic replay and branching, and the graph/trace/context CLI + Mermaid visualization. Use whenever the user asks to log, trace, monitor, debug, persist, resume, replay, branch, or visualize a reactifact pipeline, or to serve a traces/metrics UI or dashboard.
---

# Observability, persistence and replay

Everything a run does is already structured — artifacts, relations, patches,
turns. Observability is a matter of *subscribing* to it: pass sinks to the
`Runtime`, and the same data powers traces, metrics, replay and diagrams.

## Traces

Attach a tracer (or several — `tracer=[a, b]` composes):

```python
# not-run: illustrative
from reactifact.tracing import TraceStore
from reactifact import Runtime

store = TraceStore("traces.db", max_runs=200)
runtime = Runtime(ctx, agents=agents, tracer=store)
await runtime.arun()
```

A `RunTrace` holds `outcome`, `duration_ms`, and one `AgentSpan` per agent run
with its `reads`/`writes` (`ArtifactRef`: `data_type`, `op_type`, truncated
`data`), `relations` (`RelationRef`), `llm_calls` (`LLMCall`: provider, model,
tokens, latency, `prompt_hash`, error) and `error`. `store.query(session_id=…,
outcome=…, tags=…, q=…)` reads runs back; `store.tag_runs(...)` /
`list_tags(...)` annotate them for review. Sinks: `OTLPTracer` (OpenTelemetry)
and `LangfuseTracer`; `TraceStore` also has a `PostgresStore` variant.

## Metrics

`Metrics` is a dependency-free in-process collector; `MetricsTracer` fills it
from the same tracing hook, so one `Runtime` gives both traces and metrics:

```python
import asyncio
from pydantic import BaseModel
from reactifact import (
    Consume, Context, Produce, Runtime, RuntimeResources, create_agent,
)
from reactifact.metrics import Metrics, MetricsTracer


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    text: str


class Answerer(Produce[Answer]):
    artifact_type = Answer

    async def produce(self, call):
        call.effects.create(Answer(text=call.trigger.data.text))


agent = create_agent("answerer", consumes=[Consume(Question)], produces=[Answerer()])

metrics = Metrics()
ctx = Context(resources=RuntimeResources(metrics=metrics))
ctx.create(Question(text="hi"))
asyncio.run(Runtime(ctx, agents=[agent], tracer=MetricsTracer(metrics)).arun())

assert 'reactifact_runs_total{outcome="completed"} 1' in metrics.render()
```

Counters cover run/budget outcomes, agent runs, LLM calls/tokens/errors/cost
(with a `pricer`), and artifact/relation counts; histograms cover agent and LLM
latency. `metrics.render()` is the Prometheus text exposition (no
`prometheus_client`); `create_metrics_router(metrics)` serves `GET /metrics`
(the `web` extra). For app-specific signals, `RuntimeResources(metrics=...)`
exposes the same sink to produces (`call.context.resources.metrics.increment(
"route_total", route=...)`) — it is a no-op until configured.

## Sessions and durability

A `Session` binds a `Context` to a key-value store so a restart resumes from the
last commit:

```python
# not-run: illustrative
from reactifact import SessionStore
from reactifact.checkpoints import FileKVBackend

store = SessionStore(FileKVBackend("./sessions"))
session = await store.open("user-42", resources=resources)
runtime = Runtime(session.context, agents=agents, session=session)
await runtime.arun()          # saves per commit (session_save_policy="per_commit")
# later, in a new process:
session = await store.open("user-42", resources=resources)
assert session.loaded
```

Backends: `InMemoryKVBackend`, `FileKVBackend`, `SQLiteKVBackend`,
`PostgreSQLKVBackend` (the `pg` extra). Checkpoint backends
(`FileBackend`, `SQLiteBackend`, `PostgreSQLKVBackend`) snapshot the working
tree/commit chain for branching and time travel.

## Replay and branching

- `ReplayLLM(recording, mode="record"|"replay")` records every LLM call and
  replays it without the network, raising `ReplayMiss` on divergence.
  `replay_summary(context)` reports what a run would replay.
- `python -m reactifact replay <sessions.sqlite3>` does a deterministic state
  replay of a saved session. It never re-runs the runtime, so recorded outbound
  side effects (`PendingAction`, dispatched by `Runtime(dispatcher=...)`) are
  read back — never re-sent; the summary prints `dispatched / pending / failed`
  action counts.
- Branching forks a session's state (`BranchStore` over a KV backend);
  `python -m reactifact branch <path> <session> <action>` manages forks.

## Correlated logging

`configure_logging()` (from `reactifact`) turns on structured logs under
`reactifact.*` — silent until called; `json=True` for one object per line.
Every line carries `run_id`/`session_id`/`generation`/`agent`/`request_id`;
add app fields with `bind(...)` and `get_logger(__name__)`. Logs are metadata
(no artifact/prompt content).

```python
from reactifact import bind, configure_logging, get_logger

configure_logging(level="INFO")
log = get_logger(__name__)
with bind(tenant="acme"):
    log.info("handling request")
```

## CLI and visualization

```bash
reactifact graph  examples.knowledge.agents     # static agent blueprint
reactifact trace  traces.db                     # run diagram (Mermaid)
reactifact context sessions.sqlite3             # live provenance graph
reactifact replay  sessions.sqlite3             # deterministic replay
reactifact branch  sessions.sqlite3 <session> list
reactifact skills  install                      # bundled agent skills
```

Everything prints Mermaid — paste it into GitHub, Notion or mermaid.live. In
code, `reactifact.viz.blueprint(agents)`, `trace_to_mermaid(trace)` and
`context_to_mermaid(context)` produce the same diagrams.

## Where to look

- `docs/en/observability.md`, `docs/en/durability.md`, `docs/en/replay.md`,
  `docs/en/viz.md`.
- `reactifact serve` / the tracing `web.py` router for a traces UI.
