# Quickstart — three real applications

The [front-page example](index.md) proves the idea (evidence → answer, no
graph wired between the two agents) in the smallest possible shape. This page
bridges the gap to something you'd actually build: three short, runnable
snippets — a tool-calling agent, retrieval over your own docs, and a
session-persisted chat bot — each pointing at the full, production-shaped
example it's trimmed from. Every snippet on this page was run to produce the
output shown; none of it is hypothetical.

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
`Consume`/`Produce`/`Effects` with nothing to rewrite. See
[Patterns](patterns.md) for the full model.

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

    from reactifact.tool_use import ToolAnswer
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

**Full version**: `examples/devops` — three specialist agents (k8s/GitLab/
Ansible) behind one router, a trace dashboard, a web UI. Run it:
`.venv/bin/python examples/devops/chat.py`.

## 2. Retrieval over your own docs

Sources are a capability, not a hardcoded RAG pipeline — `FileSystemSource`
here, but `CSVSource`/`EmbeddingSource`/`WebSource` plug into the exact same
`fan_out_sources` call (§8). Provenance is a first-class effect
(`materialize_doc` links the derived `Doc` back to its `SourceRef`), not a
citation string built by hand:

```python
import asyncio
from pydantic import BaseModel

from reactifact import Consume, Context, Runtime, RuntimeResources, create_agent
from reactifact.produce import produce
from reactifact.recipes import fan_out_sources, materialize_doc, find, keyword_score
from reactifact.sources import FileSystemSource, SourceRef


class Question(BaseModel):
    text: str


class Doc(BaseModel):
    text: str


class Answer(BaseModel):
    text: str
    sources: list[str] = []


@produce(SourceRef)
async def search(call):
    question = find(call.inputs, Question)
    if question is None:
        return None
    await fan_out_sources(call.context, question.data.text, owner_id=question.id, limit=3)


@produce(Doc)
async def resolve(call):
    ref = find(call.inputs, SourceRef)
    if ref is None:
        return None
    await materialize_doc(call.context, ref, lambda ctx, ref, content: Doc(text=content))


@produce(Answer)
async def answer(call):
    docs = [a for a in call.inputs if isinstance(a.data, Doc)]
    if not docs:
        return None
    d = docs[0]
    sources = call.context.related(d.id, "materialized_from")
    return Answer(text=d.data.text.strip(), sources=[s.data.locator for s in sources])


search_agent = create_agent("search", consumes=[Consume(Question)], produces=[search])
resolve_agent = create_agent("resolve", consumes=[Consume(SourceRef)], produces=[resolve])
answer_agent = create_agent("answer", consumes=[Consume(Doc)], produces=[answer])


async def main():
    resources = RuntimeResources(
        # the dict key and source_id= must match — that's how a SourceRef
        # finds its way back to the source that produced it (resources.get_source)
        sources={"docs": FileSystemSource("./docs", source_id="docs", scorer=keyword_score)},
    )
    ctx = Context(resources=resources)
    runtime = Runtime(ctx, agents=[search_agent, resolve_agent, answer_agent])
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

`ChatAssistant` owns sessions, the turn loop, and history reconstruction —
your app supplies only the domain hooks. The same object works as a plain
async call (`invoke`) or, mounted via `reactifact.web.create_chat_router`, as an
SSE endpoint on your own FastAPI app:

```python
import asyncio
from pydantic import BaseModel

from reactifact import Consume, RuntimeResources, SessionStore, create_agent
from reactifact.checkpoints import FileKVBackend
from reactifact.chat import ChatAssistant
from reactifact.produce import produce


class UserMsg(BaseModel):
    text: str
    session_id: str = ""


class Reply(BaseModel):
    query_id: str
    text: str


@produce(Reply)
async def echo(call):
    msg = call.trigger
    if msg is None:
        return None
    return Reply(query_id=msg.id, text=f"you said: {msg.data.text}")


echo_agent = create_agent("echo", consumes=[Consume(UserMsg)], produces=[echo])


def reply(ctx, msg_id):
    latest = ctx.latest(Reply)
    return {"reply": latest.data.text if latest else ""}


async def main():
    store = SessionStore(FileKVBackend("./sessions"))
    assistant = ChatAssistant(
        store=store,
        agents=[echo_agent],
        user_message=UserMsg,
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
the same reason: `ChatAssistant` closes a callable-built `RuntimeResources`
after every turn automatically, so a real provider's HTTP client never leaks.

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
- [Examples](examples.md) — all fourteen, with what each one specifically teaches.
- [Port matrix](port-matrix.md) — if you know LangGraph/CrewAI, which example
  maps to which pattern you already know.
