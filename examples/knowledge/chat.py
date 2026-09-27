"""Console multi-source knowledge chat (a «reply from patches» demo).

Run:  .venv/bin/python examples/knowledge/chat.py
Without an LLM it works on deterministic fallbacks; for real generators:
  export OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_MODEL=qwen2.5:7b
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # run as a script — add src to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from examples.knowledge.agents import AGENTS
from examples.knowledge.models import Answer, ChatReply, ResearchTurn, UserQuery
from reactifact import Budget, Runtime, RuntimeResources, SessionStore
from reactifact.checkpoints import FileKVBackend
from reactifact.providers import openai_llm, openrouter_llm
from reactifact.recipes import keyword_score, load_skills
from reactifact.sources import CSVSource, FileSystemSource

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

KNOWLEDGE_DOCS = ROOT / "docs"
KNOWLEDGE_SKILLS = ROOT / "skills"

#: Sentinel: resolve the LLM from the environment (the demo default).
_UNSET = object()


def build_llm() -> Any:
    """Explicit provider for this demo: OpenRouter (default) or a local
    OpenAI-compatible endpoint; `None` when no key is configured → the demo
    runs offline on deterministic fallbacks."""
    import os

    if os.getenv("OPENROUTER_API_KEY"):
        return openrouter_llm(max_tokens=2048)
    if os.getenv("OPENAI_BASE_URL"):
        return openai_llm(
            base_url=os.getenv("OPENAI_BASE_URL", ""),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", ""),
            max_tokens=2048,
        )
    return None


def build_resources(llm: Any = _UNSET) -> RuntimeResources:
    """The demo's sources (+ optional LLM). `llm`: from env by default;
    pass an explicit provider or `None` for hermetic tests/fallbacks."""
    if llm is _UNSET:
        llm = build_llm()
    resources = RuntimeResources(
        llm=llm,
        sources={
            "guide": FileSystemSource(
                str(KNOWLEDGE_DOCS / "guide"),
                source_id="guide",
                scorer=keyword_score,
            ),
            "pricing": FileSystemSource(
                str(KNOWLEDGE_DOCS / "pricing"),
                source_id="pricing",
                scorer=keyword_score,
            ),
            "costs": CSVSource(str(KNOWLEDGE_DOCS / "costs"), source_id="costs"),
        },
    )
    # Skills (§67, reactifact.recipes.skills): loaded once, matched per-turn in
    # BuildAnswer against the situation, not the raw question.
    resources.set("skills", load_skills(KNOWLEDGE_SKILLS))
    return resources


async def main() -> None:
    resources = build_resources()
    if resources.llm is None:
        print("LLM not configured — demo runs on deterministic fallbacks.")
        print("For generation set OPENAI_BASE_URL / OPENAI_MODEL / OPENAI_API_KEY.\n")

    store = SessionStore(FileKVBackend(str(ROOT / "sessions")))
    session = await store.open("knowledge", resources=resources)
    message = "Session restored" if session.loaded else "New session"
    print(f"[{message}]\n")

    runtime = Runtime(
        session.context,
        agents=AGENTS,
        session=session,
        budget=Budget(max_runs=80),
        max_concurrency=4,
    )

    while True:
        text = input("You: ").strip()
        if text.lower() in {"exit", "quit"}:
            break
        if text.lower() == "new":
            await session.delete()
            print("New session.\n")
            continue
        if not text:
            continue

        query = session.context.create(UserQuery(text=text, session_id="knowledge"))

        async for event in runtime.astream():
            if event.kind == "status":
                print(f"   {event.message}")
            elif event.kind == "run_end" and event.data.get("outcome") != "completed":
                print(f"   [stopped: {event.data.get('outcome')}]")

        answers = [
            a
            for a in session.context.list_artifacts(Answer)
            if a.data.query_id == query.id
        ]
        if answers:
            latest_answer = max(answers, key=lambda a: a.created_at)
            print("\nAnswer:")
            print(latest_answer.data.text)
            if latest_answer.data.sources:
                print("Sources:")
                for source in latest_answer.data.sources:
                    print("  •", source)
        else:
            turns = [
                t
                for t in session.context.list_artifacts(ResearchTurn)
                if t.data.query_id == query.id
            ]
            if turns and turns[0].data.status == "insufficient":
                print("\n→ Nothing found in the documentation. Rephrase your question.")
            else:
                replies = [
                    r
                    for r in session.context.list_artifacts(ChatReply)
                    if r.data.query_id == query.id
                ]
                if replies:
                    latest_reply = max(replies, key=lambda r: r.created_at)
                    print("\n→", latest_reply.data.text)
                else:
                    print("\n(failed to assemble an answer)")

        session.context.compact(keep_commits=500)  # bound a long session's history
        await session.save()


if __name__ == "__main__":
    asyncio.run(main())
