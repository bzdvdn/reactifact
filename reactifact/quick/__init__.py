"""reactifact.quick — the 80% on-ramp: batteries-included agents, no ceremony.

`reactifact.quick` is *sugar*, not a second framework. Every object it returns
is built from the same primitives the rest of reactifact uses
(`Context`/`Artifact`/`Effects`/`Patch`/`Produce`/`Consume`), and exposes the
real `Agent` (`.agent`/`.agents`) plus the `Context` a run produced
(`.context`) — so the moment you need more than the facade offers, you graduate
to hand-written produces without rewriting anything (see `docs/en/quickstart.md`
for the ladder).

Four entry points cover the recurring first tasks:

    from reactifact.quick import agent, rag, tools_agent, chat_agent

    # 1) one structured LLM call
    qa = agent(system="Answer in one sentence.", schema=AnswerBody)
    body = await qa.ask("What are the three states of water?")

    # 2) retrieval over your own files, with provenance
    r = rag({"docs": "./docs", "costs": "./costs.csv"})
    answer = await r.ask("what's the total gpu cost?")   # answer.text, answer.sources

    # 3) an LLM with tools
    t = tools_agent(system="You are ops.", tools=[check_status])
    text = await t.ask("is checkout-api healthy?")

    # 4) a session-persisted chat assistant
    c = chat_agent(agents=[...], llm=provider)

Every entry point takes `llm=<LLMProvider>` (default `from_env()`).

Nothing here hides the artifact model: `agent(...)` produces artifacts with
provenance like any other produce; `rag(...)` links every answer `supported_by`
the documents it used. The facade only removes the boilerplate of assembling
those produces for the common case. For anything beyond it, write produces by
hand — see `docs/en/patterns.md`.

Layout: each entry point is its own module (`agent`, `rag`, `tools_agent`,
`chat`), with the shared artifact models in `models` and the cross-cutting
helpers in `_shared` — all re-exported here, so the public import surface is
just `reactifact.quick`.
"""

from __future__ import annotations

from .agent import QuickAgent, agent
from .chat import chat_agent
from .models import Answer, Doc, Question
from .rag import RAG_ANSWER_SYSTEM, AnswerBuilder, DocBuilder, QuickRAG, rag
from .tools_agent import QuickToolsAgent, tools_agent

__all__ = [
    "Answer",
    "AnswerBuilder",
    "Doc",
    "DocBuilder",
    "Question",
    "QuickAgent",
    "QuickRAG",
    "QuickToolsAgent",
    "RAG_ANSWER_SYSTEM",
    "agent",
    "chat_agent",
    "rag",
    "tools_agent",
]
