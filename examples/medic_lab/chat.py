"""Console medic-lab: hypothesis laboratory with human steering (§60).

Run:  .venv/bin/python examples/medic_lab/chat.py

The evidence pool is the local `pages/*.md` fixtures by default; set
MEDIC_LAB_URLS (comma-separated) to also investigate live web pages.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from reactifact import Budget, Context, Runtime, RuntimeResources
from reactifact.providers import openai_llm, openrouter_llm
from reactifact.sources import FileSystemSource, Source, WebSource
from reactifact.tracing import Tracer, TraceStore

from examples.medic_lab.agents import medic_lab_agents, medic_lab_scheduler
from examples.medic_lab.models import Question, ResearchReport

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
MAX_TURNS = 40

#: Sentinel: «resolve the LLM from the environment», the demo default.
_UNSET = object()


def _usable_llm() -> Any | None:
    """Env LLM, unless the configured key is still a placeholder (e.g.
    `sk-or-v1-...` from .env.example) — then no model, so the demo falls back
    deterministically instead of silently calling an invalid key.

    The lab enables reasoning by default (`MEDIC_LAB_REASONING=off` to disable):
    hypothesis work benefits from chain-of-thought, unlike chat-style demos.
    """
    import os

    if os.getenv("OPENAI_BASE_URL"):
        llm = openai_llm(
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("OPENAI_MODEL"),
            max_tokens=2048,
        )
    else:
        llm = None
    if llm is None:
        reasoning_on = os.getenv("MEDIC_LAB_REASONING", "on").strip().lower() not in {
            "off",
            "0",
            "false",
            "no",
        }
        provider = openrouter_llm(
            extra_body={"reasoning": {"enabled": True}} if reasoning_on else {}
        )
        llm = provider
    key = getattr(llm, "api_key", None)
    if not key or "..." in str(key):
        return None
    return llm


def build_resources(llm: Any = _UNSET) -> RuntimeResources:
    """Sources (+ optional live web). `llm` = from env by default; pass an
    explicit provider or `None` (no model — deterministic fallbacks) explicitly
    for hermetic tests."""
    import os

    if llm is _UNSET:
        llm = _usable_llm()
    sources: dict[str, Source] = {
        "papers": FileSystemSource(str(ROOT / "pages"), source_id="papers")
    }
    raw = os.getenv("MEDIC_LAB_URLS")
    if raw:
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        sources["web"] = WebSource(urls=urls, source_id="web")
    return RuntimeResources(llm=llm, sources=sources)


async def run_question(ctx: Context, runtime: Runtime, text: str) -> None:
    ctx.create(Question(text=text, session_id="medic-lab"))
    for _ in range(MAX_TURNS):
        async for event in runtime.astream():
            if event.kind == "status":
                print(f"   {event.message}")
        if ctx.list_artifacts(ResearchReport):
            return
        pending = ctx.latest_pending_question()
        if pending is not None:
            print(f"\n[{pending.data.kind}] {pending.data.question}")
            text = input("Your choice (or Enter to report): ").strip()
            ctx.resume(pending.id, text or "stop")


async def main() -> None:
    resources = build_resources()
    ctx = Context(resources=resources)
    trace_store = TraceStore(str(ROOT / "traces.db"))
    runtime = Runtime(
        ctx,
        agents=medic_lab_agents(),
        budget=Budget(max_runs=400),
        max_concurrency=6,
        tracer=Tracer(store=trace_store),
        scheduler=medic_lab_scheduler(),
    )
    print("medic-lab — evidence-based hypothesis laboratory.\n")
    print('Ask e.g. "does vitamin D supplementation prevent colds?" (Ctrl+C to exit)\n')

    while True:
        text = input("You: ").strip()
        if text.lower() in {"exit", "quit"}:
            break
        if not text:
            continue
        await run_question(ctx, runtime, text)

        reports = [a for a in ctx.list_artifacts(ResearchReport)]
        if reports:
            report = reports[-1].data
            print("\nReport:")
            print(report.answer)
            print("Uncertainty:", report.uncertainty)
            print("Ranking:")
            for rank in report.ranking:
                print(
                    f"  • {rank.statement[:60]}… [score {rank.score}, {rank.verdict}]"
                )
        print()


if __name__ == "__main__":
    asyncio.run(main())
