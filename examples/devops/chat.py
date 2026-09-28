"""Console devops assistant (k8s / GitLab / Ansible via LLM + tools).

Run:  .venv/bin/python examples/devops/chat.py
Without OPENROUTER_API_KEY there is no LLM — agents honestly say «Could not reach a decision».
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # running as a script — add src to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from examples.devops.agents import (
    AnsibleAgent,
    GitlabAgent,
    K8sAgent,
    RenderAgent,
    RouteAgent,
)
from examples.devops.guardrails import devops_guardrail_policy
from examples.devops.models import ChatReply, UserMsg
from reactifact import Budget, Runtime, RuntimeResources, SessionStore
from reactifact.checkpoints import FileKVBackend
from reactifact.guardrails import GuardrailViolation
from reactifact.providers import openai_llm, openrouter_llm


def build_llm() -> Any | None:
    """Explicit provider for this demo: OpenRouter (default) or a local
    OpenAI-compatible endpoint; `None` when no key is configured → offline."""
    import os

    if os.getenv("OPENROUTER_API_KEY"):
        return openrouter_llm(max_tokens=2048)
    if os.getenv("OPENAI_BASE_URL"):
        return openai_llm(
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("OPENAI_MODEL"),
            max_tokens=2048,
        )
    return None


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


async def main() -> None:
    llm = build_llm()
    if llm is None:
        print(
            "LLM is not configured — agents won't be able to answer. Set OPENROUTER_API_KEY."
        )

    resources = RuntimeResources(llm=llm, guardrails=devops_guardrail_policy())
    store = SessionStore(FileKVBackend(str(ROOT / "sessions")))
    session = await store.open("devops", resources=resources)
    print("New session" if not session.loaded else "Session restored")
    print(
        "I'll help with k8s, GitLab, Ansible. For example: «why is the pod crashing?»\n"
    )

    runtime = Runtime(
        session.context,
        agents=[RouteAgent(), K8sAgent(), GitlabAgent(), AnsibleAgent(), RenderAgent()],
        session=session,
        budget=Budget(max_runs=200, max_tool_calls=12),
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

        msg = session.context.create(UserMsg(text=text, session_id="devops"))

        try:
            async for event in runtime.astream():
                if event.kind in ("status", "agent"):
                    print(f"   {event.message}")
        except GuardrailViolation as exc:
            print(f"\nRequest refused by a guardrail: {exc}\n")
            continue

        replies = [
            r
            for r in session.context.list_artifacts(ChatReply)
            if r.data.query_id == msg.id
        ]
        if replies:
            latest = max(replies, key=lambda r: r.created_at)
            print(f"\n{latest.data.text}\n")
        else:
            print("\n(failed to assemble the answer)\n")

        session.context.compact(keep_commits=500)  # bound a long session's history
        await session.save()


if __name__ == "__main__":
    asyncio.run(main())
