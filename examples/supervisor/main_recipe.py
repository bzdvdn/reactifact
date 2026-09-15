"""supervisor — the same demo as `main.py`, built on `recipes.Router` +
`recipes.ApprovalGate` instead of hand-rolled `RouteTask`/`Supervisor`
produces.

`main.py`'s `Supervisor.produce` (46 lines) tracks the *whole* history of a
thread's approval questions itself, not just the unanswered ones — get that
wrong and a rejection spawns a duplicate question chain. `ApprovalGate` owns
that; this file only implements the four decisions that actually need
judgement: `classify`, `fallback_route`, `on_approve`, `on_reject`. The
`Specialist` step (actually doing the routed work) stays exactly as
hand-written as in `main.py` — nothing about "what a route does" is generic.

Also note: `ApprovalGate` asks with `kind="approve"`, not `main.py`'s
`kind="approval"` — the same vocabulary the destructive-tool gate
(`tool_use.py`) and `Verify` (`verify.py`) use, so one control-plane UI
handles all three.

    uv run python -m examples.supervisor.main_recipe
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from pydantic import BaseModel
from reactifact import (
    Agent,
    Artifact,
    Consume,
    Context,
    Event,
    PendingQuestion,
    Produce,
    Runtime,
    RuntimeResources,
)
from reactifact.prompts import PromptTemplate
from reactifact.providers import LLMProvider
from reactifact.recipes import ApprovalGate, Router
from reactifact.structured import structured_llm


def build_llm() -> LLMProvider | None:
    """Explicit provider for this demo: OpenRouter (default) or a local
    OpenAI-compatible endpoint; `None` when no key is configured -> offline."""
    import os

    from reactifact.providers import openai_llm, openrouter_llm

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


ROUTES = ("budget", "timeline", "quality")


class Request(BaseModel):
    text: str = ""


class RouteBody(BaseModel):
    route: str


class Task(BaseModel):
    thread: str = ""
    route: str = ""


class SpecialistReport(BaseModel):
    thread: str = ""
    route: str = ""
    text: str = ""


class FinalReply(BaseModel):
    thread: str = ""
    text: str = ""


class _Text(BaseModel):
    text: str


_ROUTE = PromptTemplate(
    """You classify a request. Reply with a single route: {routes}."""
)
_SPECIALIST = PromptTemplate(
    """You are the '{route}' specialist. Answer the request briefly and
actionably, strictly in scope of your specialty."""
)


class MyRouter(Router[Request, Task]):
    request_type = Request
    task_type = Task
    routes = ROUTES

    async def classify(
        self, context: Context, request: Artifact[Request]
    ) -> str | None:
        body = await structured_llm(
            context,
            schema=RouteBody,
            system=_ROUTE.render(routes=", ".join(ROUTES)),
            user=request.data.text,
        )
        return body.route if body is not None else None

    def fallback_route(self, context: Context, request: Artifact[Request]) -> str:
        low = request.data.text.lower()
        for route in self.routes:
            if route in low:
                return route
        return "quality"


class Specialist(Produce[SpecialistReport]):
    """Domain work for a route — nothing here is generic, stays hand-written."""

    artifact_type = SpecialistReport

    async def produce(
        self,
        context: Context,
        inputs: list[Artifact[Any]],
        event: Event | None = None,
    ) -> None:
        task = context.get(event.artifact_id) if event is not None else None
        if task is None or not isinstance(task.data, Task):
            return None
        body = await structured_llm(
            context,
            schema=_Text,
            system=_SPECIALIST.render(route=task.data.route),
            user=task.data.thread,
        )
        request = context.get(task.data.thread)
        user_text = request.data.text if request is not None else task.data.thread
        text = (
            body.text
            if body is not None
            else f"(offline {task.data.route}) «{user_text[:80]}»"
        )
        report = self.effects.create(
            SpecialistReport(thread=task.data.thread, route=task.data.route, text=text),
            id=f"report:{task.data.thread}",
        )
        report.link("from_task", task)
        return None


class MyGate(ApprovalGate[SpecialistReport, FinalReply]):
    report_type = SpecialistReport
    final_type = FinalReply

    def approval_question(
        self, context: Context, report: Artifact[SpecialistReport]
    ) -> str:
        return f"Approve the specialist's answer ({report.data.route})?"

    async def on_approve(
        self, context: Context, report: Artifact[SpecialistReport]
    ) -> FinalReply:
        return FinalReply(text=report.data.text)

    async def on_reject(
        self, context: Context, report: Artifact[SpecialistReport], answer: str
    ) -> FinalReply:
        return FinalReply(text="Rejected: please refine the request and try again.")


class Flow(Agent):
    name = "supervisor_recipe"
    consumes = [
        Consume(Request),
        Consume(Task),
        Consume(SpecialistReport),
        Consume(PendingQuestion),
    ]
    produces = [
        MyRouter().produce(),
        Specialist(),
        MyGate().produce(),
        Produce(PendingQuestion),  # widens allowed Create types, see verify.py
    ]


def run(
    *,
    text: str = "Optimize the lighting and socket budget.",
    llm: LLMProvider | None = None,
) -> Context:
    ctx = Context(resources=RuntimeResources(llm=llm))
    ctx.create(Request(text=text))
    asyncio.run(Runtime(ctx, agents=[Flow()]).arun())  # → the approval question waits

    pending = [q for q in ctx.pending_questions() if q.data.kind == "approve"]
    if pending:
        # simulate the human answer, same idiom `main.py` uses (§60)
        ctx.resume(pending[0].id, "yes")
        asyncio.run(Runtime(ctx, agents=[Flow()]).arun())
    return ctx


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.supervisor.main_recipe")
    parser.add_argument("--text", default="Optimize the lighting and socket budget.")
    args = parser.parse_args()

    ctx = run(text=args.text, llm=build_llm())
    replies = ctx.list_artifacts(FinalReply)
    reports = ctx.list_artifacts(SpecialistReport)
    print("supervisor (recipe) · route → specialist → HITL approval")
    for r in reports:
        print(f"  [{r.data.route}] {r.data.text}")
    for rep in replies:
        print(f"  reply: {rep.data.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
