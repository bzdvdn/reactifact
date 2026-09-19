"""starter_app — a copy-pasteable app over the four `quick` cases.

One FastAPI process you can clone and run, covering the shapes most apps start
with:

- **agent**  — `POST /api/ask {mode:"agent"}` one structured LLM call
- **rag**    — `POST /api/ask {mode:"rag"}` retrieval over `knowledge/`, with citations
- **tools**  — `POST /api/ask {mode:"tools"}` an LLM that can call a tool (`add`)
- **chat**   — `POST /api/chat` a session-persisted assistant (`ChatAssistant`)
- plus a **trace dashboard** at `/traces` for every run above.

**Provider selection** is `reactifact.providers.from_env()`, so the same code
runs three ways with no edits:

- no key          → offline / deterministic honest fallbacks
- `OPENROUTER_API_KEY`      → OpenRouter
- `OPENAI_BASE_URL` (+ `OPENAI_API_KEY`, `OPENAI_MODEL`) → any OpenAI-compatible
  endpoint (OpenAI, a local vLLM/Ollama, …)

Copy `app.py` + `models.py` + `knowledge/` + `web/` and change the domain; the
wiring is what you keep.

Run:  .venv/bin/python -m examples.starter_app.app
      # then open http://127.0.0.1:8000  (UI) and http://127.0.0.1:8000/traces
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # run as a script — add the repo root to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from examples.starter_app.models import (
    AnswerBody,
    AskRequest,
    AskResponse,
    ChatReply,
    ChatRequest,
    ChatResponse,
)
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from reactifact import (
    Consume,
    LLMProvider,
    ProduceCall,
    create_agent,
    produce,
)
from reactifact.quick import Doc, Question, agent, chat_agent, rag, tools_agent
from reactifact.structured import llm_reply
from reactifact.tools import tool
from reactifact.tracing import Tracer, TraceStore
from reactifact.tracing.web import create_trace_router
from reactifact.web import create_chat_router

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

#: Shown when no provider is configured or a call fails — never a fake answer.
OFFLINE_REPLY = (
    "No model configured. Set OPENROUTER_API_KEY, or OPENAI_BASE_URL + "
    "OPENAI_API_KEY, in examples/starter_app/.env — or use the offline "
    "'rag' mode, which still cites your documents."
)


@tool
async def add(a: float, b: float) -> str:
    """Add two numbers and return the sum."""
    return str(a + b)


@produce(ChatReply)
async def _chat_reply(call: ProduceCall) -> None:
    question = call.trigger
    if question is None or not isinstance(question.data, Question):
        return None
    text = await llm_reply(
        call.context,
        system="You are a concise, friendly assistant.",
        user=question.data.text,
    )
    call.effects.create(ChatReply(text=text or OFFLINE_REPLY))
    return None


def _from_env() -> LLMProvider | None:
    from reactifact.providers import from_env

    return from_env()


def create_app(
    *,
    store_dir: str | Path | None = None,
    llm: LLMProvider | None = None,
) -> FastAPI:
    """Builds the app. `llm`/`store_dir` are for tests; by default the provider
    comes from the environment and state lives under this example's directory."""
    directory = Path(store_dir) if store_dir else ROOT
    provider = llm if llm is not None else _from_env()
    trace_store = TraceStore(str(directory / "traces.db"))
    tracer = Tracer(store=trace_store)

    structured_agent = agent(
        "Answer the user's question in one short sentence.",
        AnswerBody,
        llm=provider,
        tracer=tracer,
    )
    rag_app = rag({"docs": ROOT / "knowledge"}, llm=provider, tracer=tracer)
    agent_with_tools = tools_agent(
        "Use the add tool whenever arithmetic is needed.",
        [add],
        llm=provider,
        tracer=tracer,
    )
    chat_agent_instance = chat_agent(
        [create_agent("chat", consumes=[Consume(Question)], produces=[_chat_reply])],
        store=str(directory / "sessions"),
        llm=provider,
        tracer=tracer,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()

    app = FastAPI(title="reactifact starter app", lifespan=lifespan)
    app.state.provider = provider

    @app.get("/api/provider")
    async def provider_info() -> dict[str, Any]:
        if provider is None:
            return {"provider": None, "mode": "offline"}
        return {"provider": type(provider).__name__, "mode": "model"}

    @app.post("/api/ask", response_model=AskResponse)
    async def ask(req: AskRequest) -> AskResponse:
        if req.mode == "agent":
            body = await structured_agent.ask(req.question)
            return AskResponse(mode="agent", text=body.text if body else OFFLINE_REPLY)
        if req.mode == "rag":
            answer = await rag_app.ask(req.question)
            if answer is not None:
                return AskResponse(mode="rag", text=answer.text, sources=answer.sources)
            # No provider: the pipeline still retrieved and materialized the
            # documents (`.context` is the graduation path) — answer with the
            # matched passages themselves, still cited, instead of a shrug.
            docs = (
                rag_app.context.list_artifacts(Doc)
                if rag_app.context is not None
                else []
            )
            if docs:
                return AskResponse(
                    mode="rag",
                    text="\n\n".join(d.data.text.strip() for d in docs[:3]),
                    sources=[d.data.locator for d in docs],
                )
            return AskResponse(mode="rag", text=OFFLINE_REPLY)
        text = await agent_with_tools.ask(req.question)
        return AskResponse(mode="tools", text=text or OFFLINE_REPLY)

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        result = await chat_agent_instance.invoke(
            req.message, session_id=req.session_id
        )
        return ChatResponse(
            session_id=req.session_id, reply=str(result.get("reply", ""))
        )

    # canonical chat contract (SSE /api/chat/stream, /api/runs, /api/health)
    app.include_router(create_chat_router(chat_agent_instance))
    app.include_router(
        create_trace_router(
            trace_store,
            username=os.environ.get("TRACE_USER") or None,
            password=os.environ.get("TRACE_PASSWORD") or None,
        )
    )

    web_dir = ROOT / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "examples.starter_app.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
    )
