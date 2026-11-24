# Observability

Every run records a trace. Traces are *observable by default*: the demo
dashboards work offline with no external services, and you can additionally ship
traces to Langfuse or Postgres.

## What a trace contains

A `RunTrace` is one run of the runtime (until `astream`/`run` ends):

- **Agent spans** (`AgentSpan`) — what each agent did: artifact reads and
  writes, the key/value summary of the patch it produced, the consumed artifact
  type that triggered it (`event_artifact_type`), which produces actually ran
  (`produces`: name, authored operations, latency), and which tools it executed
  (`tools`: `ToolRun` name/produce/latency/status/chars/output — args are
  opt-in, output is truncated/redacted by the trace policy).
  The dashboard, OTLP (`reactifact.tools`) and Langfuse
  (`langfuse.observation.metadata.tools`) carry the same list; Langfuse/OTLP
  also emit a child span per tool, placed by `ToolRun.started_at` (parallel
  calls overlap). Tool args are omitted by default — set
  `RuntimeResources(record_tool_args=True)` to include them (scrubbed by
  `redactor=`). `Tool.execute` is instrumented on the class, so a direct call
  inside a step lands in the same `tools` list as `execute_tool`.
- **Agent contract** (`AgentContract`) — the declared `consumes`/`produces`/
  `tools`/`priority`, plus the concrete `event_artifact_id`. The Work view
  renders explicit `consumes → produces` lists, mutes declared-but-not-run
  items, and a click on any chip filters the visible spans.
- **LLM calls** (`LLMCall`) — prompts, responses (truncated), token usage,
  latency, and the step that made the call (`LLMCall.step`), so the waterfall
  nests it under that step.
- **Timing and ordering** — the whole causal chain of a run, in order. Agent
  spans, steps (`StepRun.started_at`), LLM calls (`LLMCall.started_at`) and tools
  (`ToolRun.started_at`) carry wall-clock starts, so the timeline can place
  parallel work and nested calls without guessing.

A huge run is read in pages: `get(span_limit=…, span_offset=…)` (default 500)
returns `span_total`/`has_more_spans`, the detail UI loads more on demand, and
Mermaid is built only for a capped window. Write-time caps
(`Tracer(limits=TraceLimits(...))`) mark a trace `trace_truncated` instead of
letting it grow without bound. `DELETE /api/traces/{id}` and
`DELETE /api/sessions/{id}` (or `delete_run`/`delete_session`) remove a bad run
or session; the session itself stays visible until you choose to delete it.

`RecordingLLM` wraps your provider for free, so **LLM call tracking needs no
code** — just wire it at resources time.

## Storing and viewing

### SQLite store + web dashboard (local, offline)

```python
from reactifact.tracing import TraceStore

store = TraceStore("traces.db")   # SQLite sink; also serves runs back to the UI
```

The store's interface is async (`export`/`query`/`get`); the SQLite core runs in
a worker thread, so the same object works in a web app and in plain sync code.

The dashboard is a FastAPI router mounted on your app:

```python
from reactifact.tracing.web import create_trace_router

app.include_router(create_trace_router(store), prefix="/traces")
```

It serves a small app shell (shared `templates/app.css`): a collapsible sidebar
(Traces / Sessions), `/traces` (list of runs, filterable), `/traces/{id}` (stat
tiles, spans, reads, writes, LLM input/output, timing, a native **causal
chain** (runtime → agent → step → LLM/tool → commit) and a live Mermaid
**evidence graph** — written artifacts with `patch.link` provenance edges,
§34), and `/sessions`. The
`devops` example mounts this router and is the reference UI.

![Traces list: filterable by outcome and session, each row showing duration and span count.](../img/tracer-list.png)

Opening a run starts with the overview — wall time, work, spans, LLM/tool time,
errors and tokens:

![Run overview: duration, work, spans, LLM/tool time, errors and tokens.](../img/tracer-run-card.png)

The **Work** card has two views. **Tree** groups spans by agent and shows the
declared `Consume → Step` contract plus what actually ran; no-op steps are
dimmed, and clicking a chip filters the visible spans:

![Work tree: spans grouped by agent, with the Consume → Step contract.](../img/tracer-run-detail.png)

**Waterfall** puts `span → step → LLM/tool` on one time axis, so parallel work
and the latency bottleneck are visible at a glance:

![Waterfall: span, step, LLM and tool bars on a shared time axis.](../img/tracer-timeline.png)

**Branches** appears when the run has a parent or children: it links to the
parent run and lists child runs (branch, outcome, duration) fetched from
`/api/traces/{id}/children`. The list API also accepts `branch=` and
`parent_run_id=` filters, and child ordering is oldest-first — no client-side
tree building needed.

**Causal chain** is the readable "why did this happen" view: `Runtime → agent`
on the triggering artifact, then each step with its LLM/tool calls, then the
commit. Click an event to focus and outline its span in Tree/Waterfall:

![Causal chain: runtime → agent → step → LLM/tool → commit, with steps named.](../img/tracer-causal.png)

### Portable reports (no FastAPI)

A trace is not tied to the dashboard. `RunTrace` renders itself:

```python
trace = await store.get(run_id)
print(trace.to_text())        # terminal
print(trace.to_markdown())    # PR comment / CI summary
print(trace.to_html())        # one self-contained file
```

The CLI mirrors this and can compare two runs:

```bash
reactifact trace traces.db <run_id> --format text
reactifact trace traces.db <run_id> --format html --out trace.html
reactifact trace traces.db <run_id> --against <other_id>
```

`Pipeline(traces=True)` or `traces="traces.db"` records every run without
building a `Tracer` by hand; `result.trace_id` and `await result.trace()` read
it back. This is the non-FastAPI path: scripts, notebooks and CI use the same
trace store.

### Retention (disk and audit)

`RetentionPolicy` controls what the store deletes:

```python
from reactifact.tracing import RetentionPolicy

store = TraceStore(
    "traces.db",
    retention=RetentionPolicy(
        max_runs=5000,            # newest unprotected runs
        max_age_days=30,          # TTL, even inside max_runs
        keep_failed=True,         # never delete failed/stopped
        keep_tags=frozenset({"incident", "bad-prompt"}),
    ),
)
```

The policy runs after every export (and on demand). The CLI is the ops path:

```bash
reactifact traces prune traces.db --keep 5000 --days 30 --dry-run
reactifact traces prune traces.db --keep 5000 --days 30 --keep-failed --keep-tag incident
```

Protected runs (failed, tagged) do not count against `max_runs`; they can make
the store larger, by design.

### Sampling (production volume)

`Tracer(sample=...)` applies tail sampling before any sink sees the run:

```python
from reactifact.tracing import SamplingPolicy, Tracer

tracer = Tracer(
    store=store,
    sample=SamplingPolicy(
        rate=0.01,                       # 1% of successful runs
        always_on_error=True,            # keep all failed/stopped runs
        always_if=lambda t: t.duration_ms > 3000,
    ),
)
```

Sampling is deterministic by run id, so a given run is consistently in or out
of the sample. Use `rate=1.0` for dev/test and a low rate plus
`always_on_error` in production.

### Reviewing runs: tags and notes

The dashboard is also the **review surface**. After inspecting a trace you mark
the run with a tag (`bad-prompt`, `hallucination`, `needs-review`, …) and an
optional note describing what is wrong / what to change. Tags live in the store,
not in the runtime — they are annotations attached to a `run_id` *after* the
fact, so the immutable trace the runtime produced is never rewritten.

- The list page shows tag facets with counts (click to filter), tag chips per
  run, free-text search (run / session / agent / error), sortable columns
  (click a header), token and latency rollups, a live toggle and a JSON export
  of the current filter. Every filter/tag/sort/page lives in the URL, so a view
  is a **shareable link**; name one and it is kept as a **saved view**.
- A **sessions** page (`/sessions`) groups runs by `session_id` with run count,
  total duration, tokens and last-seen, and links into the filtered trace list.
- The run page has a **Review** card: add/remove tags, each with a note.
- **Tree ↔ Waterfall**: the run's work is shown either grouped by agent, or as a
  hierarchical Gantt waterfall (`span → step → LLM/tool`) with start offset and
  width by latency, making parallelism and the latency bottleneck obvious.
  Clicking a causal-chain event jumps to and outlines its span in the active
  Tree/Waterfall view.
- In-trace search filters spans/agents/LLM text; **copy link**, **copy JSON**
  and a raw **JSON** view are one click away.
- Select several runs to tag or untag them in bulk.
- `manage tags` renames, recolours or deletes a tag across every run.

```text
GET    /api/traces?tag=bad-prompt&tag=review&tag_mode=all&q=refund&sort=duration_ms
GET    /api/tags                       # facets: name, colour, count
POST   /api/traces/{id}/tags           # {tag, note}
DELETE /api/traces/{id}/tags/{tag}
POST   /api/traces/tags                # bulk: {run_ids, tag, note}
POST   /api/traces/tags/remove         # bulk: {run_ids, tag}
PATCH  /api/tags/{name}                # {new_name, color}
DELETE /api/tags/{name}
GET    /api/traces/export              # full traces for the current filter
GET    /api/sessions                   # runs grouped by session, with rollups
```

Both backends implement the same annotation API: SQLite keeps `tags`/`run_tags`
tables (created and migrated automatically), Postgres mirrors them as
`TEXT`/`TIMESTAMPTZ` with cascading deletes. Point `create_trace_router` at
either and the review workflow behaves identically.

### Configurable columns from artifacts

Give the traces table columns pulled from the run's artifacts — e.g. the user's
question and the final answer:

```python
from reactifact.tracing import TraceColumn

app.include_router(create_trace_router(
    store,
    columns=[
        TraceColumn(label="Question", agent="route", type=UserMsg,
                    field="text", direction="read"),
        TraceColumn(label="Answer", type=ChatReply, field="text"),
    ],
))
```

A `TraceColumn` names the column and where its value comes from: which agent's
span, which artifact `type` (a class or its name), `direction` (`read` / `write`
/ `any`), and a dotted `field` into the artifact's data. A list step takes its
first element unless an explicit index is given, so `sources.title` means
`sources[0].title` and `sources.1.title` the second. Several matches: `index`
defaults to the last. A missing artifact, a `—` default, or JSON cut off by the
trace size limit all fall back to `default`.

`scope="session"` resolves a column **as of** the row's run: it looks at every
run up to and including that one, in chronological order, instead of just the
row's run. The read order is reversed, so the triggering artifact (recorded
first) is treated as the most recent and the default `index=-1` means "latest
so far". A chat is many turns, and a HITL clarify splits one exchange into two
runs (the ask turn, then the resume turn) — so `Question` = the message that
started the current exchange (`index=-1`), shown on the resume row too, and
`Answer` = the latest reply so far (`—` until it exists).

Columns are computed server-side per run and returned in each `/api/traces`
item as `fields` (`GET /api/columns` lists the labels). The dashboard renders
them in the list (with a **column chooser**, visibility persisted per browser)
and, on the run page, as a **Fields** card — so the question/answer (and a HITL
clarify, e.g. a `PendingQuestion` column) are visible on the run too, not just
the list. Values
come from the JSON persisted with each span, clipped by
`TraceLimits(max_data_chars=…)` on the tracer (preferred) or the legacy
`RuntimeResources(trace_truncate=…)` (default 1500 chars; `None` disables the
clip) — raise it if a deep/large field must resolve.

The `devops` and `repair` examples wire this up (`Question` + `Answer`, and the
latter also `Stage` + a pending HITL `Pending`).

### Langfuse and Postgres as additional sinks

A trace can go to several places at once via `CompositeTracer`, passed to the
`Runtime`. `Tracer`s are thin: `on_turn_begin` → `on_span` → `on_turn_end`
(only `on_turn_end` performs I/O; sinks `export` asynchronously).

```python
from reactifact import Runtime
from reactifact.tracing import LangfuseTracer, PostgresStore, TraceStore

runtime = Runtime(
    ctx,
    agents=[...],
    tracer=[
        TraceStore("traces.db"),                        # local dashboard
        LangfuseTracer(public_key="...", private_key="...",
                       host="https://cloud.langfuse.com"),
        PostgresStore(dsn="postgresql://…"),            # pg extra required
    ],
)
```

Spans are delivered once per turn end (delivery-once semantics); the Langfuse
tracer maps read/write summaries into `input`/`output`, so the timeline is
readable in their UI; each produce the agent ran also becomes a child
observation, so the waterfall shows the `Consume → Step` steps. The SQLite
`TraceStore` stays a source for the web dashboard; Postgres mirrors the same
`runs`/`spans` schema.

Both `TraceStore` and `PostgresStore` implement `TraceReader` (async
`query`/`get`), so `create_trace_router` works against **either**: point the
dashboard at `PostgresStore(dsn)` to view traces written to Postgres without a
local SQLite file.

### `OTLPTracer`: any OTLP/HTTP collector

`LangfuseTracer` is Langfuse-specific (its own `langfuse.*` attribute
namespace). `OTLPTracer` exports the same run as vendor-neutral spans —
GenAI semantic-convention attributes only (`gen_ai.*`, plus a
`reactifact.*` namespace for reads/writes/provenance) — to any OTLP/HTTP
collector: Jaeger, Tempo, Honeycomb, Datadog Agent, a local
`otel-collector`, ...

```python
from reactifact.tracing import OTLPTracer

runtime = Runtime(
    ctx,
    agents=[...],
    tracer=OTLPTracer(endpoint="http://localhost:4318/v1/traces"),
)
```

No `opentelemetry-sdk` dependency — like `LangfuseTracer`, it POSTs the
OTLP/HTTP JSON payload directly over `httpx` (already a core dependency), so
this needs no extra to install. Pass `headers=` for a collector that
requires auth.

**LangSmith** ingests OTLP and maps the `gen_ai.*` conventions:

```python
OTLPTracer(
    endpoint="https://api.smith.langchain.com/otel/v1/traces",
    headers={
        "x-api-key": os.environ["LANGSMITH_API_KEY"],
        "Langsmith-Project": "my-project",   # optional (default: "default")
    },
)
```

Self-hosted LangSmith uses `https://<your-host>/api/v1/otel/v1/traces` with the
same headers.

**SigNoz** takes OTLP/HTTP directly — no key self-hosted:

```python
# self-hosted
OTLPTracer(endpoint="http://<signoz-host>:4318/v1/traces")
# SigNoz Cloud
OTLPTracer(
    endpoint="https://ingest.<region>.signoz.cloud:443/v1/traces",
    headers={"signoz-ingestion-key": os.environ["SIGNOZ_INGESTION_KEY"]},
)
```

The same works for Jaeger, Tempo, Honeycomb, Datadog and a local
`otel-collector`. To fan out to several back ends at once — or to redact
before export at the collector — point `endpoint` at an OpenTelemetry Collector
and let it route; `RuntimeResources.redactor` already strips trace text before
it leaves the process (see [Redacting sensitive data](#redacting-sensitive-data)).

## Metrics (Prometheus)

Traces answer "what happened in *this* run"; metrics answer "how are runs doing
overall" — the numbers you alert on. `reactifact.metrics` has no dependency
beyond the core: an in-process collector plus a `Tracer` that fills it, and a
`/metrics` router.

```python
from reactifact.metrics import Metrics, MetricsTracer, create_metrics_router

metrics = Metrics()
runtime = Runtime(ctx, agents=GROUPS, tracer=MetricsTracer(metrics))
app.include_router(create_metrics_router(metrics))   # GET /metrics
```

It records, per finished turn: `reactifact_runs_total{outcome}`,
`reactifact_budget_exceeded_total{outcome}`, `reactifact_agent_runs_total{
agent,status}`, `reactifact_agent_latency_seconds{agent}`,
`reactifact_llm_calls_total{provider,model,agent}`,
`reactifact_llm_tokens_total{kind,provider,model}`,
`reactifact_llm_errors_total{provider,model}`,
`reactifact_tool_calls_total{tool,status}`,
`reactifact_tool_errors_total{tool}`,
`reactifact_tool_latency_seconds{tool}`, and — with
`MetricsTracer(metrics, pricer=…)` — `reactifact_llm_cost_total{provider,model}`.

Straight from the span's reads/writes/edges you also get **domain-adjacent**
counters with no app code — `reactifact_artifacts_written_total{type,op}`,
`reactifact_artifacts_read_total{type}`, `reactifact_relations_total{relation}`
— so whatever artifact types and relations the app uses (e.g. an
`Answer`/`Evidence` pair and `supported_by`) show up automatically.

For app-specific signals (a route, a refusal, a source), record them from a
produce through `RuntimeResources.metrics` — a no-op sink until configured, so
there is no `None` to check:

```python
metrics = Metrics()
runtime = Runtime(
    Context(resources=RuntimeResources(metrics=metrics)),
    agents=GROUPS,
    tracer=MetricsTracer(metrics),
)

# inside a produce, at the decision point:
call.context.resources.metrics.increment("route_total", route=route)
call.context.resources.metrics.observe("docs_found", len(docs), source=source)
```

`Metrics.render()` emits the Prometheus text exposition directly (no
`prometheus_client`), so `create_metrics_router` just serves it; `MetricsTracer`
also composes with the sinks (`tracer=[TraceStore(…), MetricsTracer(metrics)]`).
As with traces, only **state-changing** agents emit spans, so a settle-only
generation isn't counted.

## Emission model

- Only **state-changing** agents emit spans (a pure read/verify produce emits
  nothing — less noise).
- One `Tracer` per `CompositeTracer`: hand the same composite to multiple sinks.
- `on_turn_end` is the single delivery point: a new `astream`/`arun` advances the
  run id (a re-run counts as a new run), and each turn's trace is finalized once.

### Live lifecycle hooks

A finished `RunTrace` is the source of truth, but a UI often needs to show work
*while it happens*. `Tracer` exposes optional, sync, best-effort hooks:

```python
class LiveTracer(Tracer):
    def on_agent_start(self, agent, event, reads): ...
    def on_agent_retry(self, agent, attempt, error): ...
    def on_tool_start(self, tool, args): ...   # args is redacted or None
    def on_tool_end(self, run): ...             # ToolRun
    def on_commit(self, commit): ...            # after the patch is applied
```

They are called alongside the existing `on_turn_begin`/`on_span`/`on_turn_end`.
Keep them non-blocking (enqueue to a bounded queue; do the I/O elsewhere): an
exception in a hook is logged and swallowed, so observability can never fail the
run.

### Streaming LLM calls

`RecordingLLM` records `complete()` and `stream()` calls. A streamed call is
recorded **once**, after the stream ends (or is closed), with the concatenated
text, terminal token usage and any error; the stored response is clipped to
`trace_truncate` (default 1500). The per-chunk overhead is one async-generator
frame, no per-token recording.

### Typed run events (`astream_events`)

`Runtime.astream()` is the legacy status stream (`ProgressEvent`: run start,
status text, run end). For a live UI/Studio, use `astream_events`, which yields
typed `RunEvent`s and selects what you need:

```python
async for event in runtime.astream_events(
    stream_mode=["debug", "updates", "values", "messages"]
):
    match event:
        case AgentStart(agent=name): ...
        case ArtifactWrite(writes=writes): ...
        case LLMDelta(delta=text): ...
        case RunEnd(outcome=outcome): ...
```

| mode | events |
| --- | --- |
| `custom` | `CustomEvent` — `Context.announce(...)` |
| `updates` | `ArtifactWrite` after each committed patch |
| `values` | `Values` — context diff after each commit |
| `debug` | `GenerationStart`, `AgentStart`, `AgentRetry`, `AgentEnd`, `ToolStart`, `ToolEnd` |
| `messages` | `LLMDelta` chunks (streamed, or one final delta for `complete()`) |

`RunStart`, `RunEnd` and `RunFailed` are always emitted; a failed run yields
`RunFailed` and then re-raises the original exception. Events are best-effort:
the internal queue is bounded and drops the oldest item rather than
back-pressuring the runtime.

### Workspace scope and metadata

A shared trace store can serve several workspaces. Stamp runs either on the
runtime or per request:

```python
Runtime(..., trace_scope="workspace-42")
# or, per turn:
await runtime.arun(request={
    "workspace_id": "workspace-42",
    "trace_metadata": {"user_id": "u1", "agent_version": "v3"},
})
```

`RunTrace.scope` and `RunTrace.metadata` are stored with the run;
`store.query(scope="workspace-42")` and `store.sessions(scope=...)` filter by it
(also exposed as `?scope=` on the dashboard API). `trace_scope` wins over
`request["workspace_id"]`.

## Logging

reactifact writes structured, **correlated** logs under the `reactifact.*`
logger namespace and stays silent until you turn it on (a library never
configures the root logger):

```python
import sys

from reactifact import configure_logging

configure_logging()               # INFO, human-readable, to stderr
configure_logging(json=True)      # one JSON object per line
configure_logging(level="DEBUG", stream=sys.stderr)
```

Every line carries the turn's correlation fields — `run_id`, `session_id`,
`generation`, `agent`, and the `request_id` from `runtime.arun(request=…)` — so
a line ties back to the exact run/agent. Add your own with `bind(...)`:

```python
from reactifact import bind, get_logger

log = get_logger(__name__)

with bind(tenant="acme", user="bob"):
    log.info("handling request")
```

Levels: `run started` / `run finished` and `action dispatched` are `INFO`;
per-generation and per-agent completion are `DEBUG`, and a failing agent or a
failed dispatch is `WARNING`. Logs are **metadata** — artifact data and prompt
contents are never logged. Logging complements tracing: a `RunTrace` is the
durable audit of a run; these logs are what an operator tails while it happens.

## Tracing never fails the run

Observability is **best-effort by contract**. A sink that is unreachable
(Langfuse down, Postgres refusing connections), a custom `Tracer` callback that
raises, or a malformed trace payload is caught, logged as a warning, and
skipped — the business run completes normally and the next turn is traced as
usual. When several sinks or tracers are configured, a failing one never
prevents the others from receiving the trace. Losing observability should never
mean losing the run it was only supposed to observe.

## Redacting sensitive data

Traces leave the process (SQLite, Postgres, Langfuse, a dashboard, a log
line); the working `Context` — and, when a session is saved, the resumable
conversation — does not. Set `redactor=` on `RuntimeResources` to scrub the
text that *leaves*: artifact `data`, LLM `messages`/`response`, and span/LLM
`error`. The live state and persisted sessions are deliberately **not**
redacted — masking the working copy would corrupt a conversation that is
supposed to resume.

```python
from reactifact import RuntimeResources
from reactifact.redaction import RegexRedactor

resources = RuntimeResources(llm=from_env(), redactor=RegexRedactor())
```

The built-in `RegexRedactor` is conservative on purpose — email, US SSN, IBAN,
`Bearer` tokens and `sk-…`-style API keys — so it never masks a legitimate
financial figure; pass `patterns=[(name, regex), …]` for a project's own
formats (cards, phones, …). Any object with a `redact(text: str) -> str` method
satisfies the hook, so a PII service drops in without subclassing. `None` (the
default) leaves traces byte-for-byte as before.

Tool observations are *not* trace text — they go back into the model's prompt.
If tools can return secrets, pass `redact_tool_outputs=True` to
`recipes.run_tool_loop`: it applies the same `resources.redactor` before the
observation is fed to the LLM and recorded on `ToolLoopResult.observations`.

## Progress events (UI reactivity)

For the animated "Думаю… / Составляю план… / Считаю смету…" lines, `Step`s
call `context.announce(message, kind=..., **payload)`. Those become
`ProgressEvent`s on the `EventHub`, which the runtime yields to the SSE stream:

```python
async for event in runtime.astream():
    if event.kind == "status":
        yield sse("status", {"message": event.message})
```

`announce` is not a log — it is a *first-class UI channel* that the web demos
consume verbatim.