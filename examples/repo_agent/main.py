"""repo_agent — a coding agent whose destructive actions need human approval.

An LLM + tools agent over a tiny repository: safe tools (`read_file`,
`run_tests`) run immediately, but `git_commit` is `@tool(destructive=True)` —
the model may *decide* to call it, but the runtime turns that into a
`PendingQuestion(kind="approve")` and only executes the commit once a human
approves (§60). A denied call never runs.

Offline: a scripted provider stands in for the model, and the tools are local
(one reads from `sample_repo/`, one returns a canned test summary, the commit is
recorded in memory) so the demo needs no network and no git.

Run:  .venv/bin/python -m examples.repo_agent.main
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script — add the repo root to sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from collections.abc import AsyncIterator

from pydantic import BaseModel
from reactifact import (
    Consume,
    Context,
    PendingQuestion,
    Produce,
    ProduceCall,
    Runtime,
    RuntimeResources,
    tool,
)
from reactifact.llm_agent import HITLLMAgent
from reactifact.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
)
from reactifact.tool_use import ToolAnswer

REPO = Path(__file__).parent / "sample_repo"

#: Commits "made" by the destructive tool — a list, since this demo has no git.
COMMITS: list[str] = []


@tool
async def read_file(path: str) -> str:
    """Read a file from the repository."""
    target = (REPO / path).resolve()
    if target != REPO.resolve() and REPO.resolve() not in target.parents:
        return "error: path is outside the repository"
    if not target.is_file():
        return f"error: {path} not found"
    return target.read_text(encoding="utf-8")


@tool
async def run_tests() -> str:
    """Run the repository's test suite and return a short summary."""
    return "5 passed in 0.02s"


@tool(destructive=True)
async def git_commit(message: str) -> str:
    """Commit the staged changes. Destructive — requires human approval."""
    COMMITS.append(message)
    return f"committed: {message}"


class Ticket(BaseModel):
    text: str


class ChangeReport(BaseModel):
    summary: str
    commits: list[str] = []


class BuildChangeReport(Produce[ChangeReport]):
    artifact_type = ChangeReport

    async def produce(self, call: ProduceCall) -> None:
        answer = call.trigger
        if answer is None or not isinstance(answer.data, ToolAnswer):
            return None
        self.effects.create(
            ChangeReport(summary=answer.data.text, commits=list(COMMITS))
        )
        return None


class RepoAgent(HITLLMAgent):
    name = "repo"
    system = (
        "You are a coding agent. Read files and run tests freely; commit only "
        "when the ticket asks you to."
    )
    tools = [read_file, run_tests, git_commit]
    consumes = [Consume(Ticket)]
    produces = [BuildChangeReport()]


class ScriptedLLM(LLMProvider):
    """Decides from a script: which tool to call, then when to answer."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = (
            self.responses.pop(0)
            if self.responses
            else '{"type":"answer","text":"done"}'
        )
        return LLMResponse(text=text)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMResponseChunk]:
        yield LLMResponseChunk(text="")


def _context(script: list[str]) -> tuple[Context, Runtime]:
    context = Context(resources=RuntimeResources(llm=ScriptedLLM(script)))
    runtime = Runtime(context, agents=[RepoAgent()])
    return context, runtime


async def main() -> int:
    print(">>> safe tool: the agent runs the tests with no approval needed")
    context, runtime = _context(
        [
            '{"type":"tool_call","tool":"run_tests","args":{}}',
            '{"type":"answer","text":"Tests pass (5)."}',
        ]
    )
    context.create(Ticket(text="run the tests"), id="t1")
    await runtime.arun()
    report = context.latest(ChangeReport)
    assert report is not None
    print(f"report: {report.data.summary}  commits={report.data.commits}")
    assert context.list_artifacts(PendingQuestion) == []

    print("\n>>> destructive tool: the commit is gated behind approval")
    COMMITS.clear()
    context, runtime = _context(
        [
            '{"type":"tool_call","tool":"git_commit","args":{"message":"fix add()"}}',
            '{"type":"answer","text":"Committed the fix."}',
        ]
    )
    context.create(Ticket(text="fix the calculator and commit it"), id="t2")
    await runtime.arun()

    questions = context.list_artifacts(PendingQuestion)
    assert len(questions) == 1 and questions[0].data.kind == "approve"
    print(f"pending approval: {questions[0].data.question!r}")
    print(f"commits so far: {COMMITS}  (nothing ran yet)")
    assert COMMITS == []

    context.resume(questions[0].id, "yes")
    await runtime.arun()
    report = context.latest(ChangeReport)
    assert report is not None
    print(f"after approval — commits: {report.data.commits}")
    print(f"report: {report.data.summary}")
    assert COMMITS == ["fix add()"]

    print("\n[confirmed] the destructive action ran only after a human said yes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
