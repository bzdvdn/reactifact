"""support_copilot: grounded reply with citations, or a real escalation."""

import asyncio

from examples.support_copilot.agents import AGENTS
from examples.support_copilot.main import build_context
from examples.support_copilot.models import Question, Reply
from reactifact import Budget, Runtime


def _run(question: str, question_id: str):
    context = build_context()
    runtime = Runtime(context, agents=AGENTS, budget=Budget(max_runs=40))
    context.create(Question(text=question), id=question_id)
    asyncio.run(runtime.arun())
    return context


def test_grounded_reply_cites_the_matched_document():
    context = _run("how do refunds work?", "q1")
    reply = context.latest(Reply)
    assert reply is not None
    assert reply.data.escalated is False
    assert reply.data.citations == ["faq.md"]
    assert "14 days" in reply.data.text

    docs = context.related(reply.id, "supported_by")
    assert docs and docs[0].data.locator == "faq.md"


def test_uncovered_question_escalates_instead_of_guessing():
    context = _run("can I pay with bitcoin?", "q2")
    assert context.latest(Reply) is None  # nothing invented
    pending = context.latest_pending_question()
    assert pending is not None
    assert pending.data.kind == "escalate"


def test_human_answer_finalizes_the_reply():
    context = _run("can I pay with bitcoin?", "q2")
    pending = context.latest_pending_question()
    assert pending is not None
    resumed = context.resume(pending.id, "We don't accept bitcoin yet.")
    assert resumed is not None

    runtime = Runtime(context, agents=AGENTS, budget=Budget(max_runs=10))
    asyncio.run(runtime.arun())

    reply = context.latest(Reply)
    assert reply is not None
    assert reply.data.escalated is True
    assert reply.data.text == "We don't accept bitcoin yet."
