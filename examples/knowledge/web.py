"""FastAPI + SSE for a knowledge chat (multi-source assistant, CTXSPACE).

The transport is the canonical reactifact chat contract (owned by `reactifact.chat`
+ the router in `reactifact.web`):

  event: session  — session id
  event: status   — progress («Searching for info…», «Found N…», «Assembling the answer…»)
  event: message  — terminal reply {reply, waiting, sources}

This file only supplies the domain hooks: the agent list, the input artifact
type and the terminal reply builder (Answer → ChatReply → honest fallback).

Run:  .venv/bin/python examples/knowledge/web.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # run as a script — add src to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from examples.knowledge.agents import AGENTS
from examples.knowledge.chat import _UNSET, build_resources
from examples.knowledge.models import (
    Answer,
    Calculation,
    ChatReply,
    Claim,
    UserQuery,
)
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from reactifact import Budget, SessionStore
from reactifact.chat import ChatAssistant, ChatMemory
from reactifact.checkpoints import FileKVBackend
from reactifact.recipes.conversation import MessageSpec, Transcript
from reactifact.web import create_chat_router

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

FALLBACK_REPLY = "Failed to assemble an answer. Try rephrasing the question."


def terminal_reply(ctx: Any, msg_id: str) -> dict:
    """Terminal reply for a message: Answer → ChatReply → insufficient."""
    answers = [a for a in ctx.list_artifacts(Answer) if a.data.query_id == msg_id]
    if answers:
        answer = max(answers, key=lambda a: a.created_at).data
        claims = [
            {
                "text": c.data.text,
                "confidence": c.data.confidence,
                "status": "conflict" if c.data.conflict else c.data.status,
            }
            for c in ctx.list_artifacts(Claim)
            if c.data.query_id == msg_id
        ]
        calculations = [
            {
                "description": c.data.description,
                "value": c.data.value,
                "column": c.data.column,
            }
            for c in ctx.list_artifacts(Calculation)
            if c.data.query_id == msg_id
        ]
        return {
            "reply": answer.text,
            "waiting": False,
            "sources": answer.sources,
            "claims": claims,
            "calculations": calculations,
        }
    replies = [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == msg_id]
    if replies:
        latest = max(replies, key=lambda r: r.created_at)
        return {"reply": latest.data.text, "waiting": False, "sources": []}
    return {"reply": FALLBACK_REPLY, "waiting": False, "sources": []}


def _answer_text(answer: Any) -> str:
    """Assistant render: the answer prose plus a trailing Sources block."""
    text = answer.text
    if answer.sources:
        text += "\n\nSources:\n" + "\n".join(f"• {s}" for s in answer.sources)
    return text


#: The thread is three artifact types; `Transcript` merges them by role and
#: renders the `Answer` side (prose + sources) via `MessageSpec.render`.
_TRANSCRIPT = Transcript(
    [
        MessageSpec(UserQuery, "user"),
        MessageSpec(ChatReply, "assistant"),
        MessageSpec(Answer, "assistant", render=_answer_text),
    ]
)


def session_state(ctx: Any) -> dict:
    return _TRANSCRIPT.state(ctx)


def _build_assistant(llm: Any = _UNSET, store_dir: str | None = None) -> ChatAssistant:
    store = SessionStore(
        FileKVBackend(str(Path(store_dir) if store_dir else ROOT / "sessions"))
    )
    return ChatAssistant(
        store=store,
        agents=AGENTS,
        user_message=UserQuery,
        reply=terminal_reply,
        session_state=session_state,
        resources=lambda: build_resources(llm=llm),
        budget=Budget(max_runs=200),
        max_concurrency=4,
        # Bound a long-lived session's commit history (artifacts untouched).
        memory=ChatMemory(compact_commits=500),
    )


def create_app(db=None, llm: Any = _UNSET, store_dir: str | None = None) -> FastAPI:
    """App factory. `llm` and `store_dir` are for tests; the default resolves
    providers from .env (OpenRouter·DeepSeek for chat). `db` is kept for CLI
    compatibility."""
    app = FastAPI(title="knowledge-ai (reactifact)")
    app.include_router(
        create_chat_router(_build_assistant(llm=llm, store_dir=store_dir))
    )

    web_dir = ROOT / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "examples.knowledge.web:create_app", factory=True, host="127.0.0.1", port=8000
    )
