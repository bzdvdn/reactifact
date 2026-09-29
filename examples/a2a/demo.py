"""Runnable A2A demo — a reactifact agent served over A2A and called back.

Everything runs in-process (`httpx.ASGITransport`), so there is no network and
no LLM. Run it:

    .venv/bin/python -m examples.a2a.demo

It shows, in order: the Agent Card, a direct `A2AClient.send`, a remote agent
scheduled as a local node (`remote_agent`), and the HITL round-trip (a remote
task that comes back `input-required` and is resumed locally).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # running as a script — add src to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
from fastapi import FastAPI
from reactifact import Consume, Context, Runtime, RuntimeResources
from reactifact.a2a import A2AClient, Task, remote_agent

from examples.a2a.models import Answer, Question
from examples.a2a.server import create_clarifier_app, create_upper_app


def _client(app: FastAPI) -> tuple[A2AClient, httpx.AsyncClient]:
    """An `A2AClient` wired to `app` in-process (no socket)."""
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://a2a"
    )
    return A2AClient("http://a2a/", client=http), http


async def run_demo() -> dict[str, Any]:
    """Runs every step and returns the observed results (used by the test too)."""
    result: dict[str, Any] = {}
    client, http = _client(create_upper_app())
    clarifier, clarifier_http = _client(create_clarifier_app())
    try:
        card = await client.fetch_agent_card()
        result["card_name"] = card.name
        result["skills"] = [skill.id for skill in card.skills]

        # 1) Direct call: message/send → a completed task with the reply artifact.
        task = await client.send("hello a2a")
        assert isinstance(task, Task)
        result["direct_state"] = task.status.state
        result["direct_text"] = task.artifacts[0].parts[0].text

        # 2) The remote agent as a node: the local Runtime schedules it.
        node = remote_agent(
            "upper", client, consumes=[Consume(Question)], output_type=Answer
        )
        ctx = Context(resources=RuntimeResources())
        ctx.create(Question(text="run via node"))
        await Runtime(ctx, agents=[node]).arun()
        answer = ctx.latest(Answer)
        assert answer is not None
        result["node_text"] = answer.data.text

        # 3) HITL: the remote task pauses (input-required) → a local PendingQuestion.
        hitl = remote_agent(
            "clarifier", clarifier, consumes=[Consume(Question)], output_type=Answer
        )
        ctx2 = Context(resources=RuntimeResources())
        ctx2.create(Question(text="please format this"))
        runtime = Runtime(ctx2, agents=[hitl])
        await runtime.arun()
        pending = list(ctx2.pending_questions())
        assert pending, "the remote agent should have asked a question"
        result["hitl_question"] = pending[0].data.question

        ctx2.resume(pending[0].id, "markdown")
        await runtime.arun()
        final = ctx2.latest(Answer)
        assert final is not None
        result["hitl_answer"] = final.data.text
        return result
    finally:
        await http.aclose()
        await clarifier_http.aclose()


def main() -> None:
    result = asyncio.run(run_demo())
    print("A2A example — in-process, no network, no LLM\n")
    print(
        f"  Agent Card          name={result['card_name']!r} skills={result['skills']}"
    )
    print(
        "  A2AClient.send    -> "
        f"state={result['direct_state']} text={result['direct_text']!r}"
    )
    print(f"  remote_agent node -> Answer(text={result['node_text']!r})")
    print(f"  input-required    -> question={result['hitl_question']!r}")
    print(f"  after resume      -> Answer(text={result['hitl_answer']!r})")


if __name__ == "__main__":
    main()
