"""support_copilot — answer from the docs, or escalate, never hallucinate.

A support agent over a help corpus. Two honest outcomes, no third:

- the docs cover the question → a **grounded** reply citing the document it came
  from (`supported_by` provenance);
- nothing matches → the runtime **escalates to a human** with
  `effects.ask(...)` (a `PendingQuestion`), and when the human answers, that
  answer becomes the reply — the agent never invents one.

No model is required: the reply is the matched document's own text. With a
provider configured the same pipeline could paraphrase, but the citation and the
escalation gate are structural, not prompt-dependent.

Run:  .venv/bin/python -m examples.support_copilot.main
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script — add the repo root to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reactifact import Budget, Context, Runtime, RuntimeResources
from reactifact.recipes import keyword_score
from reactifact.sources import FileSystemSource

from examples.support_copilot.agents import AGENTS
from examples.support_copilot.models import Question, Reply

DATA = Path(__file__).parent / "data"


def _score(text: str, query: str) -> float:
    return keyword_score(text, query, fold_plurals=True)


def build_context() -> Context:
    resources = RuntimeResources(
        sources={"docs": FileSystemSource(str(DATA), source_id="docs", scorer=_score)}
    )
    return Context(resources=resources)


async def _ask(text: str, question_id: str) -> Context:
    context = build_context()
    runtime = Runtime(context, agents=AGENTS, budget=Budget(max_runs=40))
    context.create(Question(text=text), id=question_id)
    await runtime.arun()
    return context


async def main() -> int:
    grounded = await _ask("how do refunds work?", "q1")
    reply = grounded.latest(Reply)
    assert reply is not None
    print("question: how do refunds work?")
    print(f"reply: {reply.data.text}")
    print(f"citations: {reply.data.citations}  escalated={reply.data.escalated}")
    assert not reply.data.escalated and reply.data.citations

    print("\n>>> a question the docs do NOT cover — the agent escalates:")
    escalated = await _ask("can I pay with bitcoin?", "q2")
    assert escalated.latest(Reply) is None
    pending = escalated.latest_pending_question()
    assert pending is not None
    print(f"pending question: {pending.data.question!r} (kind={pending.data.kind})")
    print("no reply was invented — a human answer is required.")

    print("\n>>> a human answers the escalation, and the reply is finalized:")
    resumed = escalated.resume(pending.id, "We don't accept bitcoin yet.")
    assert resumed is not None
    runtime = Runtime(escalated, agents=AGENTS, budget=Budget(max_runs=10))
    await runtime.arun()
    final = escalated.latest(Reply)
    assert final is not None
    print(f"reply: {final.data.text}  escalated={final.data.escalated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
