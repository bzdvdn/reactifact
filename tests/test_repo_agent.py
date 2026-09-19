"""repo_agent: safe tools run freely, destructive ones need approval."""

import asyncio

from examples.repo_agent.main import (
    COMMITS,
    ChangeReport,
    RepoAgent,
    ScriptedLLM,
    Ticket,
)
from reactifact import Budget, Context, PendingQuestion, Runtime, RuntimeResources


def _context(script: list[str]) -> tuple[Context, Runtime]:
    context = Context(resources=RuntimeResources(llm=ScriptedLLM(script)))
    runtime = Runtime(context, agents=[RepoAgent()], budget=Budget(max_runs=20))
    return context, runtime


def test_safe_tool_runs_without_approval():
    COMMITS.clear()
    context, runtime = _context(
        [
            '{"type":"tool_call","tool":"run_tests","args":{}}',
            '{"type":"answer","text":"Tests pass (5)."}',
        ]
    )
    context.create(Ticket(text="run the tests"), id="t1")
    asyncio.run(runtime.arun())

    report = context.latest(ChangeReport)
    assert report is not None
    assert report.data.summary == "Tests pass (5)."
    assert context.list_artifacts(PendingQuestion) == []


def test_destructive_tool_is_gated_until_approved():
    COMMITS.clear()
    context, runtime = _context(
        [
            '{"type":"tool_call","tool":"git_commit","args":{"message":"fix add()"}}',
            '{"type":"answer","text":"Committed the fix."}',
        ]
    )
    context.create(Ticket(text="fix and commit"), id="t2")
    asyncio.run(runtime.arun())

    questions = context.list_artifacts(PendingQuestion)
    assert len(questions) == 1
    assert questions[0].data.kind == "approve"
    assert COMMITS == []  # nothing ran before approval

    context.resume(questions[0].id, "yes")
    asyncio.run(runtime.arun())

    assert COMMITS == ["fix add()"]
    report = context.latest(ChangeReport)
    assert report is not None
    assert report.data.commits == ["fix add()"]
