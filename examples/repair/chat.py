"""Console repair assistant on CTXSPACE (a port of REPAIR_AI_CHAT).

Run:  .venv/bin/python examples/repair/chat.py

Keys are taken from .env (see examples/repair/.env.example):
  OPENROUTER_API_KEY / OPENROUTER_MODEL — chat via OpenRouter·DeepSeek.
Pipeline: collect → design_choice → plan → estimate → final_approval → assistant.
The estimate is deterministic from price.csv (no LLM and no embedders).
"""

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script — add src to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from examples.repair.agents import RepairFlow
from examples.repair.models import ChatReply, UserMsg
from examples.repair.services import Catalog
from reactifact import Budget, Runtime, RuntimeResources, SessionStore
from reactifact.checkpoints import FileKVBackend
from reactifact.providers import (
    OpenAICompatProvider,
    image_from_env,
    openrouter_llm,
)

load_dotenv()

ROOT = Path(__file__).resolve().parent
SESSION_ID = "repair"


def build_llm() -> OpenAICompatProvider | None:
    # OpenRouter·DeepSeek in fast mode (reasoning off).
    return openrouter_llm()


async def main() -> None:
    llm = build_llm()
    if llm is None:
        print("LLM не настроен: задайте OPENROUTER_API_KEY в .env.\n")

    resources = RuntimeResources(llm=llm)
    resources.set("catalog", Catalog(ROOT / "data" / "price.csv"))
    resources.set("images_dir", str(ROOT / "web" / "assets" / "generated"))
    images = image_from_env()
    if images is not None:
        resources.set("images", images)

    store = SessionStore(FileKVBackend(str(ROOT / "sessions")))
    session = await store.open(SESSION_ID, resources=resources)
    print("Ремонтный ассистент. Опишите комнату (тип, площадь, бюджет).\n")

    runtime = Runtime(
        session.context,
        agents=[RepairFlow()],
        session=session,
        budget=Budget(max_runs=200),
        max_concurrency=2,
    )

    while True:
        text = input("Вы: ").strip()
        if text.lower() in {"exit", "quit"}:
            break
        if text.lower() == "new":
            await session.delete()
            session = await store.open(SESSION_ID, resources=resources)
            runtime = Runtime(
                session.context,
                agents=[RepairFlow()],
                session=session,
                budget=Budget(max_runs=200),
                max_concurrency=2,
            )
            print("Новая сессия.\n")
            continue
        if not text:
            continue

        msg = session.context.create(UserMsg(text=text, session_id=SESSION_ID))
        async for event in runtime.astream():
            if event.kind == "status":
                print(f"   {event.message}")

        pending = [
            q for q in session.context.pending_questions() if q.data.kind == "approval"
        ]
        if pending:
            print("\n" + pending[0].data.question)
            print("Жду вашего ответа…")
        else:
            replies = [
                r
                for r in session.context.list_artifacts(ChatReply)
                if r.data.query_id == msg.id
            ]
            if replies:
                latest = max(replies, key=lambda r: r.created_at)
                print("\n→", latest.data.text)
                if latest.data.images:
                    print("Превью:")
                    for image in latest.data.images:
                        print("  •", image)
            else:
                print("\n(продолжаю…)")

        session.context.compact(keep_commits=500)  # bound a long session's history
        await session.save()


if __name__ == "__main__":
    asyncio.run(main())
