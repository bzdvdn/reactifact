# Quickstart — three real applications

The [front-page example](index.md) proves the idea (evidence → answer, no
graph wired between the two agents) in the smallest possible shape. This page
bridges the gap to something you'd actually build: three short, runnable
snippets — a tool-calling agent, retrieval over your own docs, and a
session-persisted chat bot — each pointing at the full, production-shaped
example it's trimmed from. Every snippet on this page was run to produce the
output shown; none of it is hypothetical.

## Install and scaffold with uv

```bash
uv init my-agent && cd my-agent
uv add reactifact                 # core + CLI; FastAPI stays optional
uv run reactifact skills install  # teach your coding agent the model
```

Or start from the one-file agent template (no web, no API key):

```bash
uvx reactifact new agent my-agent
cd my-agent && uv sync
uv run reactifact run main:pipeline "hello"
uv run reactifact trace
```

`run` writes `.reactifact/traces.db`; `trace` renders the latest run. Use
`--no-trace` to skip it.

The fastest way to see the claim end to end — a computed figure, its
provenance, and a reproducible context hash, with no API key and no files —
is the bundled demo:

```bash
uv run reactifact demo            # ~3 seconds; --plain disables colour
uv run reactifact demo --web      # same answer, provenance, contracts + traces
```

It is the same three-`@step` shape you'll write below, and the whole source is
[`reactifact/demo.py`](https://github.com/bzdvdn/reactifact/blob/master/reactifact/demo.py).
`--web` serves it locally and opens the browser; use `--no-open` in a terminal
you don't want to interrupt.

For a web app, either add the extra yourself or generate the template:

```bash
uv add "reactifact[web]"          # + FastAPI/uvicorn
uv run reactifact new app my-app  # or: uvx reactifact new app my-app
cd my-app && uv sync --extra dev
cp .env.example .env
uv run reactifact dev
```

`reactifact dev` starts the app with reload and prints the three URLs worth
opening: the app, `/contracts` and `/traces` (when mounted). Pass
`app.main:create_app` explicitly or use `--open /contracts` to open the browser.

`reactifact new app` copies the bundled FastAPI template (auth, sessions, SSE,
traces, settings-driven budgets/LLM). The generated project owns its
`reactifact[web]` dependency; the core package never requires FastAPI.

`reactifact new daemon` generates a scheduled agent loop instead: settings-driven
LLM/budget, traces, graceful shutdown, and `APP_RUN_ONCE=true` for a one-shot
smoke run.

The loop is also available as a library:

```python
import asyncio

from reactifact import Daemon, RunConfig

asyncio.run(
    Daemon(
        pipeline,
        seed_source=next_tick,   # sync/async: tick index -> seed | None
        config=RunConfig(traces=True),
        interval=60,
    ).serve()
)
# runtime settings (including tracing) live in RunConfig, not in Daemon
```

`reactifact daemon app.pipeline:pipeline --seed app.seeds:next_tick --interval 60`
runs the same thing from the CLI.

## Chat in a few lines

`Chat` is the typed facade over `ChatAssistant`: seed and reply come from the
models, sessions are persisted, and streaming/history are ready.

```python
from reactifact import Chat, Agent

chat = Chat(
    agents=[Agent("answer", steps=[search, answer])],
    store="sessions.db",
    seed_type=Question,
    answer_type=Answer,
)

reply = await chat.invoke("why is checkout down?", session_id="u1")
async for event in chat.stream("and now?", session_id="u1"):
    ...
```

`seed_factory(text, session_id, request)` supplies app metadata (a category,
tenant, user); `per_turn(text, session_id, request)` is an async context manager
for turn-scoped context vars or resource cleanup. `chat.router()` returns a
ready FastAPI `APIRouter` (SSE + history), and `chat.test_client()` is a copy
with an in-memory store and traces. A pending `PendingQuestion` is reported as
`waiting: true`; with `Chat(..., resume_pending=True)` the next user message is
its answer, so HITL needs no separate endpoint. Run settings shared with
`PipelineRunner` (`resources`, `budget`, tracing, lifespan) can be passed as
`Chat(..., config=RunConfig(...))`; explicit keywords win over the config.

For a sessionful HTTP app, `chat.http()` adds the same paths as the DEVOPS-style
API:

```python
from reactifact.web import ChatRoutes

app = chat.http(
    routes=ChatRoutes(
        invoke="/chat",          # POST /api/chat
        stream="/chat/stream",   # POST /api/chat/stream
        history="/chat/history", # GET  /api/chat/history
        clear="/chat/clear",     # POST /api/chat/clear
        clear_method="POST",
        history_messages=True,
    )
)
```

`/docs`, `/api/contracts` and `/api/traces` are mounted too; `reactifact dev`
serves it.

## One definition: `Pipeline` + `PipelineRunner`

`Pipeline` is the declarative definition — agents, routes and seed/answer models.
`PipelineRunner` owns resources, budget, tracing and execution, and builds a fresh
`Context` + `Runtime` per run.

```python
from reactifact import Agent, Pipeline, PipelineRunner, RunConfig

pipeline = Pipeline(
    agents=[
        Agent("audit", steps=[compute_spend, compute_variance, answer]),
    ],
    seed_type=Question,
    answer_type=Answer,
)
runner = PipelineRunner(pipeline)
result = runner.run(Question(text="what is the Q2 variance?"))

print(result.latest(Answer).data.text)
print(result.sources(result.latest(Answer)))
print(result.hash)
```

Async variants are `arun(seed)` and `astream(seed)`; the low-level `Context`
is still on `result.context` for branch/merge, replay and tests.

Steps can also receive resources by type, and tests can override them without
rebuilding the pipeline:

```python
@step(reads=Question)
def answer(question: Question, llm: LLMProvider) -> Answer: ...

pipeline = Pipeline(agents=[Agent("answer", steps=[answer])])
runner = PipelineRunner(pipeline, config=RunConfig(llm=default_llm))
runner.override(LLMProvider, FakeLLM())
```

For long-lived resources (HTTP clients, indexes) use a lifespan, like FastAPI:

```python
@asynccontextmanager
async def lifespan():
    yield build_resources()
    await close_resources()

async with PipelineRunner(pipeline, config=RunConfig(lifespan=lifespan)) as runner:
    result = await runner.arun(Question(text="..."))
```

Tests get an in-memory trace store and isolated overrides:

```python
client = PipelineRunner(pipeline).test_client()
client.override(LLMProvider, FakeLLM())
result = client.run(Question(text="..."))
assert asyncio.run(client.traces.query())["total"] >= 1
client.close()
```

## Small agents: `@agent`

For one function, the decorator is the shortest path. Typed parameters are the
function's reads; the return annotation is its `produces`:

```python
from pydantic import BaseModel
from reactifact.agent import agent


class Route(BaseModel):
    kind: str


@agent
async def classify(question: Question) -> Route:
    return Route(kind="billing" if "invoice" in question.text else "general")
```

`classify` is an `Agent`; mount it in a `Pipeline` or `Runtime` like any other.
Pass `@agent(consumes=[...])` when the wake condition needs a `Consume`
(`by_field`, `debounce`, an approval event, …). When the work needs several
steps, use the explicit form:

```python
from reactifact import Agent, Consume

answer = Agent(
    "answer",
    consumes=[Consume(Question), Consume(Evidence, debounce=True)],
    steps=[Scout(), Answerer()],
)
```

## One command: `check`

`reactifact check` runs the quality loop over a `Pipeline`: static contracts,
registered scenarios and a dataset evaluation, then exits non-zero if any stage
fails.

```bash
uv run reactifact check app.pipeline:pipeline \
  --scenarios app.scenarios \
  --evals evals/qa.json \
  --overall 0.8
```

It prints one line per stage and a final `check: PASSED`/`FAILED` gate; use
`--gate KEY=VALUE` for a metric-specific bar. `reactifact dev` is the server
counterpart: it starts the app and points at `/contracts` and `/traces`.

## HTTP in one line

The web adapter is optional; when you need HTTP, it comes from the same typed
pipeline:

```python
pipeline = Pipeline(
    agents=[Agent("answer", steps=[extract, classify, answer])],
    seed_type=Question,
    answer_type=Answer,
)

runner = PipelineRunner(pipeline, config=RunConfig(traces=True))
app = runner.http()
```

`reactifact dev main:app` then serves `POST /run`, `POST /stream`, `/health`,
interactive `/docs` (OpenAPI generated from `Question`/`Answer`), `/contracts`
and `/traces`. Every response carries `X-Request-ID`; `/run` adds
`X-Reactifact-Run-Id`, and domain errors use
`{"error": {"code", "message", "request_id", "trace_id"}}`. To mount it in an
existing app, use `app.include_router(pipeline.router(prefix="/agent"))`.

## 0. The 80% on-ramp: `reactifact.quick`

Most first tasks are one of four things. `reactifact.quick` wires each of them
in a few lines, while still building **real reactive agents** under the hood:

```python
from pydantic import BaseModel
from reactifact.quick import agent, rag, tools_agent, chat_agent


class AnswerBody(BaseModel):
    text: str


# 1) one structured LLM call
qa = agent(system="Answer in one sentence.", schema=AnswerBody)
body = await qa.ask("What are the three states of water?")   # AnswerBody | None

# 2) retrieval over your own files, with provenance
r = rag({"docs": "./docs", "costs": "./costs.csv"})
answer = await r.ask("what's the total gpu cost?")           # answer.text, answer.sources

# 3) an LLM with tools (human=True → asks clarifying questions instead)
t = tools_agent("You are ops. Use the tools.", [check_status])  # a @tool (§1)
text = await t.ask("is checkout-api healthy?")

# 4) a session-persisted chat assistant (store=None → in-memory)
assistant = chat_agent(agents=[...], llm=provider)
```

Every entry point takes `llm=` — any `LLMProvider` (`from_env()`,
`openai_llm(...)`, `openrouter_llm(...)`, …). It defaults to `from_env()`, so a
configured `.env` just works; pass it explicitly to choose a model/endpoint.

This is **sugar over the same primitives**, not a second framework. Every
object exposes the real `Agent` and the `Context` the run produced:

```python
qa.agent      # the reactive Agent — mount it on your own Runtime
qa.context    # the artifacts/provenance of the last ask()
```

`rag(...)` links every `Answer` `supported_by` the `Doc`s it used, and each
`Doc` `materialized_from` its `SourceRef` — the same provenance the
hand-written pipeline below builds. When the facade no longer fits, drop to
`Consume`/`Step`/`Effects` with nothing to rewrite. See
[Patterns](patterns.md) for the full model.

Prefer a notebook? [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/bzdvdn/reactifact/blob/master/examples/quickstart.ipynb)
— [`examples/quickstart.ipynb`](https://github.com/bzdvdn/reactifact/blob/master/examples/quickstart.ipynb) walks the same four cases, offline by default.

Bring your own artifact models — the facade is parameterized, not fixed:

```python
r = rag(
    {"docs": "./docs"},
    doc_type=MyDoc,          # must accept text/locator/title…
    answer_type=MyAnswer,
    # …or build them yourself and say how to read a doc's body/label:
    doc_factory=lambda ctx, ref, content: MyDoc(body=content, url=ref.data.locator),
    answer_factory=lambda text, docs: MyAnswer(answer=text, citations=[d.data.url for d in docs]),
    doc_text=lambda d: d.body,
    doc_locator=lambda d: d.url,
)
qa = agent("Answer briefly.", AnswerBody, question_type=MyQuestion)  # needs a `text` field
```

Provenance is unaffected by the model shape: an answer is still linked
`supported_by` the documents it used.

## 1. A tool-calling agent (with human-in-the-loop)

`HITLLMAgent` wires an LLM + a `Tool` list into the reactive ask/tool/answer
loop for you — the model decides whether to call a tool, ask the user a
clarifying question, or answer, one step at a time (§60). No manual "if
missing, ask" branch anywhere in your own code:

```python
import asyncio
from pydantic import BaseModel

from reactifact import Consume, Context, Runtime, RuntimeResources
from reactifact.llm_agent import HITLLMAgent
from reactifact.providers import from_env
from reactifact.tools import tool


class Ticket(BaseModel):
    text: str


@tool
async def check_status(service: str) -> str:
    """Look up the current status of a service."""
    return f"{service}: healthy, 3 replicas, 12ms p50 latency"


class OpsAgent(HITLLMAgent):
    name = "ops"
    system = "You are an ops assistant. Use check_status to answer questions about services."
    tools = [check_status]
    consumes = [Consume(Ticket)]


async def main():
    ctx = Context(resources=RuntimeResources(llm=from_env()))
    runtime = Runtime(ctx, agents=[OpsAgent()])
    ctx.create(Ticket(text="is checkout-api healthy?"))
    await runtime.arun()

    from reactifact.tools.use import ToolAnswer
    answer = ctx.latest(ToolAnswer)
    print("answer:", answer.data.text if answer else None)


asyncio.run(main())
```

No API key configured (`from_env()` returns `None`) → the agent honestly
says `"Could not reach a decision."` instead of guessing (§59) — that's not a
bug, it's the same honest-fallback contract every generative step in reactifact
follows. With a key in `.env`, the model actually decides to call
`check_status` and answers from its result. To see the full loop run
deterministically offline (no key, no guessing), swap `resources.llm` for a
scripted `LLMProvider` the way `tests/test_tools.py`'s `ScriptedLLM` does, or
reach for `reactifact.testing.ScenarioLab` if you're writing this as a test.

### Tools: injection, policies, typed results

A tool parameter annotated with a plain class is a collaborator, resolved from
`RuntimeResources` exactly like a step parameter — it never enters the model's
JSON schema. Return a pydantic model and it lands in `ToolOutput.data`; add
`retry=`/`timeout=` for transient failures:

```python
from pydantic import BaseModel
from reactifact import RetryPolicy, RuntimeResources, tool
from reactifact.providers import from_env


class Pod(BaseModel):
    name: str


class K8sClient:
    async def get(self, name: str) -> str: ...


@tool(retry=RetryPolicy(max_attempts=3, backoff=0.5), timeout=10)
async def get_pod(name: str, k8s: K8sClient) -> Pod:
    """Read one pod."""
    return Pod(name=await k8s.get(name))


resources = RuntimeResources(llm=from_env())
resources.register(K8sClient, K8sClient())
```

`execute_tool(tool, args, resources=...)` injects outside a runtime step (tests,
a web job); inside a step the current `Context` supplies the resources.
`ToolOutput.error_info` (`ToolError(retryable=True)`) is retried by the same
policy; a non-retryable error is returned to the model as data.

**Full version**: `examples/devops` — three specialist agents (k8s/GitLab/
Ansible) behind one router, a trace dashboard, a web UI. Run it:
`.venv/bin/python examples/devops/chat.py`.

## 2. Retrieval over your own docs

Sources are a capability, not a hardcoded RAG pipeline — `FileSystemSource`
here, but `CSVSource`/`WebSource` plug into the exact same `fan_out_sources`
call (§8); vector retrieval uses `reactifact.knowledge` + `IndexSource`.
Provenance is a first-class effect
(`materialize_doc` links the derived `Doc` back to its `SourceRef`), not a
citation string built by hand:

```python
import asyncio
from pydantic import BaseModel

from reactifact import Context, Runtime, RuntimeResources, Agent, step
from reactifact.recipes import fan_out_sources, materialize_doc, find, keyword_score
from reactifact.sources import FileSystemSource, SourceRef


class Question(BaseModel):
    text: str


class Doc(BaseModel):
    text: str


class Answer(BaseModel):
    text: str
    sources: list[str] = []


@step(produces=SourceRef, reads=Question)
async def search(call):
    question = call.trigger
    await fan_out_sources(call.context, question.data.text, owner_id=question.id, limit=3)


@step(produces=Doc, reads=SourceRef)
async def resolve(call):
    ref = call.trigger
    await materialize_doc(call.context, ref, lambda ctx, ref, content: Doc(text=content))


@step(produces=Answer, reads=Doc)
async def answer(call):
    docs = call.inputs
    d = docs[0]
    sources = call.context.related(d.id, "materialized_from")
    return Answer(text=d.data.text.strip(), sources=[s.data.locator for s in sources])


search_group = Agent("search", steps=[search])
resolve_group = Agent("resolve", steps=[resolve])
answer_group = Agent("answer", steps=[answer])


async def main():
    resources = RuntimeResources(
        # the dict key and source_id= must match — that's how a SourceRef
        # finds its way back to the source that produced it (resources.get_source)
        sources={"docs": FileSystemSource("./docs", source_id="docs", scorer=keyword_score)},
    )
    ctx = Context(resources=resources)
    runtime = Runtime(ctx, agents=[search_group, resolve_group, answer_group])
    ctx.create(Question(text="what is your refunds policy?"))
    await runtime.arun()
    a = ctx.latest(Answer)
    print("answer:", a.data.text if a else None)
    print("sources:", a.data.sources if a else None)


asyncio.run(main())
```

Two easy-to-hit gotchas this snippet already avoids: pass `scorer=keyword_score`
— the plain default scorer has no stop-word filtering, so a query like
"what **is** your refund**s** policy?" can rank a wrong document up on the
word "is" alone; and give `FileSystemSource` a `source_id=` that matches the
dict key you register it under in `resources.sources`, since that's the only
thing that lets a later `materialize_doc` resolve a `SourceRef` back to the
source that produced it.

**Full version**: `examples/knowledge` — file + CSV sources, evidence
extraction, claim verification, deterministic calculation, a keyword-triggered
skill. Run it: `.venv/bin/python examples/knowledge/chat.py`.

## 3. A session-persisted chat bot

`Chat` owns sessions, the turn loop, and history reconstruction — your app
supplies only the domain models and reply. The same object works as a plain
async call (`invoke`) or, mounted via `reactifact.web.create_chat_router`, as an
SSE endpoint on your own FastAPI app:

```python
import asyncio
from pydantic import BaseModel

from reactifact import Chat, RuntimeResources, SessionStore, Agent
from reactifact.checkpoints import FileKVBackend
from reactifact import step


class UserMsg(BaseModel):
    text: str
    session_id: str = ""


class Reply(BaseModel):
    query_id: str
    text: str


@step(produces=Reply, reads=UserMsg)
async def echo(call):
    msg = call.trigger
    return Reply(query_id=msg.id, text=f"you said: {msg.data.text}")


echo_group = Agent("echo", steps=[echo])


def reply(ctx, msg_id):
    latest = ctx.latest(Reply)
    return {"reply": latest.data.text if latest else ""}


async def main():
    store = SessionStore(FileKVBackend("./sessions"))
    assistant = Chat(
        store=store,
        agents=[echo_group],
        seed_type=UserMsg,
        reply=reply,
        resources=RuntimeResources,  # a fresh RuntimeResources per turn
    )
    result = await assistant.invoke("hello there", session_id="demo")
    print(result)                                    # {'reply': 'you said: hello there'}
    print(await assistant.history(session_id="demo"))  # both turns, reconstructed


asyncio.run(main())
```

Reopen the process and call `assistant.invoke(..., session_id="demo")`
again — the conversation resumes exactly where it left off, because
`FileKVBackend` persisted the whole commit chain, not just the latest reply.
To serve this over HTTP instead of calling `invoke` directly:

```python
from fastapi import FastAPI
from reactifact.web import create_chat_router

app = FastAPI()
app.include_router(create_chat_router(assistant))
# POST /api/chat/stream, GET/DELETE /api/runs/{session_id} — see docs/en/api.md
```

**Full version**: `examples/devops` or `examples/knowledge`'s `web.py` — real
agents behind the same two calls, plus a trace dashboard. `resources=` there
is a callable (`lambda: build_resources()`) exactly like this snippet, for
the same reason: `Chat` closes a callable-built `RuntimeResources`
after every turn automatically, so a real provider's HTTP client never leaks.

## Production shape: the FastAPI template

The snippets above keep the loop small. For a real web app, copy
[`templates/fastapi_chat`](../../reactifact/templates/fastapi_chat): FastAPI `Depends` auth,
scoped sessions, SSE, traces, settings-driven budgets/LLM, tests and Docker.
It depends on `reactifact[web]` itself — the core package still does not pull
FastAPI.

## Where this leaves you

Each snippet above is the smallest *reactive* version of its pattern — no
step of the pipeline calls the next one directly; every step only declares
what it `consumes`/`produces`, and the runtime derives execution from state
changes. That's the same rule the full examples run on, just with more
agents, more sources, more error paths. From here:

- [Patterns](patterns.md) — more shapes (reflection, map-reduce, supervisor, …).
- [Recipes](recipes.md) — the building blocks this page used
  (`fan_out_sources`, `materialize_doc`, `find`) plus the ones it didn't
  (`StatusMachine`, `WindowSummarizer`, `Skill`).
- [Examples](examples.md) — all thirteen, with what each one specifically teaches.
- [Port matrix](port-matrix.md) — if you know LangGraph/CrewAI, which example
  maps to which pattern you already know.
- [Agent skills](skills.md) — the official Agent Skills, installed with
  `python -m reactifact skills install`, so a coding agent (Claude Code, Cursor,
  Codex) can build *with* reactifact.
